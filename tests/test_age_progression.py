"""Batch completeness, age semantics, identity sources and draft boundaries; no model calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "skills/portrait-lineage/scripts/age_progression.py"
spec = importlib.util.spec_from_file_location("age_progression", MODULE)
growth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(growth)


class GrowthTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "family.bin").write_bytes(b"synthetic bookkeeping fixture, not an image")
        self.profiles = self.root / "profiles"
        self.profiles.mkdir()
        self.plan = {"schema_version": 1, "operation": {"years": 10}, "characters": [
            {"name": name, "character_id": cid, "source_mode": "selected_candidate",
             "source_age": age, "age_note": "test user age setting", "selection_note": "test user selected this candidate",
             "reference": {"path": "family.bin", "selector": selector}}
            for name, cid, age, selector in [("小满", "child-01", 6, "left child"), ("小夏", "child-02", 4, "right child")]]}

    def prepare(self):
        path = self.root / "growth.json"
        path.write_text(json.dumps(self.plan))
        return growth.prepare(path, self.profiles, self.root / "run")

    def approved_profile(self, age=6):
        p = {"schema_version": 1, "id": "child-01", "version": 3, "display_name": "小满", "aliases": ["满满"],
             "age_years": age, "kind": "fictional_character", "status": "approved",
             "review": {"confirmed_by": "user", "note": "test appearance"},
             "name_review": {"status": "confirmed", "confirmed_by": "user", "note": "test name"},
             "master_reference_id": "child-main",
             "references": [{"id": "child-main", "path": "../family.bin", "role": "approved_reference", "selector": "left child"}],
             "identity_views": {"status": "skipped_by_user", "approved_reference_ids": [], "confirmed_by": "user", "note": "test skip"},
             "lineage": {"sources": [{"id": "parent-a", "version": 1}, {"id": "parent-b", "version": 2}]}}
        (self.profiles / "one.json").write_text(json.dumps(p))
        return p

    def test_increment_grows_every_child_and_does_not_promote_candidates(self):
        result = self.prepare()
        self.assertEqual([(c["name"], c["target_age"]) for c in result["characters"]], [("小满", 16), ("小夏", 14)])
        for index in (1, 2):
            folder = self.root / "run" / f"character-{index:02d}"
            stage = json.loads((folder / "stage-candidate.json").read_text())
            self.assertEqual(stage["status"], "draft")
            self.assertIsNone(stage["review"]["confirmed_by"])
            self.assertFalse((folder / "result.png").exists())
        self.assertEqual(list(self.profiles.iterdir()), [])
        self.assertTrue(growth.verify(result["run_path"])["valid"])

    def test_target_age_is_not_increment(self):
        self.plan["operation"] = {"target_age": 10}
        result = self.prepare()
        self.assertEqual([c["target_age"] for c in result["characters"]], [10, 10])

    def test_last_child_missing_photo_fails_entire_preparation(self):
        self.plan["characters"][1]["reference"]["path"] = "missing.bin"
        with self.assertRaisesRegex(ValueError, "缺失"):
            self.prepare()
        self.assertFalse((self.root / "run").exists())

    def test_missing_age_cannot_be_guessed(self):
        del self.plan["characters"][1]["source_age"]
        with self.assertRaisesRegex(ValueError, "原年龄"):
            self.prepare()

    def test_missing_selector_cannot_pick_another_child(self):
        del self.plan["characters"][0]["reference"]["selector"]
        with self.assertRaisesRegex(ValueError, "selector"):
            self.prepare()

    def test_duplicate_ids_rejected_even_when_names_differ(self):
        self.plan["characters"][1]["character_id"] = "child-01"
        with self.assertRaisesRegex(ValueError, "重复"):
            self.prepare()

    def test_invalid_or_ambiguous_age_operation(self):
        for operation in ({"years": 10, "target_age": 10}, {"years": True}, {"years": -1},
                          {"years": 0}, {"years": float("nan")}, {"target_age": 4}):
            with self.subTest(operation=operation):
                self.plan["operation"] = operation
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse((self.root / "run").exists())

    def test_existing_run_cannot_be_overwritten(self):
        self.prepare()
        manifest = (self.root / "run/run.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.prepare()
        self.assertEqual((self.root / "run/run.json").read_bytes(), manifest)

    def test_changed_source_image_is_detected(self):
        result = self.prepare()
        (self.root / "family.bin").write_bytes(b"different photo")
        with self.assertRaisesRegex(ValueError, "参考文件变化"):
            growth.verify(result["run_path"])

    def test_modified_request_is_detected(self):
        result = self.prepare()
        path = self.root / "run/character-01/imagegen-request.json"
        request = json.loads(path.read_text())
        request["prompt"] += " changed"
        path.write_text(json.dumps(request))
        with self.assertRaisesRegex(ValueError, "请求与"):
            growth.verify(result["run_path"])

    def test_approved_profile_uses_child_master_not_parents_and_remains_unchanged(self):
        p = self.approved_profile()
        before = (self.profiles / "one.json").read_bytes()
        self.plan["characters"] = [{"name": "满满"}]
        result = self.prepare()
        self.assertEqual(result["characters"][0]["name"], "小满")
        run = json.loads((self.root / "run/run.json").read_text())
        self.assertEqual(run["subjects"][0]["source_version"], 3)
        self.assertEqual(run["subjects"][0]["profile_snapshot"], p)
        request = json.loads((self.root / "run/character-01/imagegen-request.json").read_text())
        self.assertEqual(request["referenced_image_paths"], [str((self.root / "family.bin").resolve())])
        self.assertEqual((self.profiles / "one.json").read_bytes(), before)

    def test_draft_profile_cannot_be_promoted_by_growth(self):
        p = self.approved_profile()
        p["status"] = "draft"
        (self.profiles / "one.json").write_text(json.dumps(p))
        self.plan["characters"] = [{"name": "小满"}]
        with self.assertRaisesRegex(ValueError, "尚未确认"):
            self.prepare()

    def test_old_age_field_is_compatible_without_rewriting_profile(self):
        p = self.approved_profile()
        p["age"] = p.pop("age_years")
        (self.profiles / "one.json").write_text(json.dumps(p))
        self.plan["characters"] = [{"name": "小满"}]
        self.assertEqual(self.prepare()["characters"][0]["target_age"], 16)

    def test_unknown_profile_age_requires_user_age_note(self):
        self.approved_profile(age=None)
        self.plan["characters"] = [{"name": "小满", "source_age": 6}]
        with self.assertRaisesRegex(ValueError, "age_note"):
            self.prepare()
        self.plan["characters"][0]["age_note"] = "test user specified 6"
        self.assertEqual(self.prepare()["characters"][0]["target_age"], 16)

    def test_conflicting_profile_age_rejected(self):
        self.approved_profile()
        self.plan["characters"] = [{"name": "小满", "source_age": 8}]
        with self.assertRaisesRegex(ValueError, "年龄与档案不一致"):
            self.prepare()


if __name__ == "__main__":
    unittest.main()
