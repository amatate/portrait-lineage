"""Named reuse must not silently switch people or promote inferred evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "skills/portrait-lineage/scripts/characters.py"
spec = importlib.util.spec_from_file_location("characters", MODULE)
characters = importlib.util.module_from_spec(spec)
spec.loader.exec_module(characters)


class CharacterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "fixture.bin").write_bytes(b"test bookkeeping only; not a photo")
        self.profile = {
            "schema_version": 1, "id": "person-001", "version": 1,
            "display_name": "阿禾", "aliases": ["禾禾"], "kind": "real_person", "status": "approved",
            "review": {"confirmed_by": "user", "note": "synthetic test approval"},
            "name_review": {"status": "confirmed", "confirmed_by": "user", "note": "synthetic test name"},
            "permission": {"reference_use": True, "note": "synthetic test permission"},
            "master_reference_id": "original",
            "references": [{"id": "original", "path": "fixture.bin", "role": "current_photo"},
                           {"id": "views", "path": "fixture.bin", "role": "inferred_view"},
                           {"id": "rejected", "path": "absent.bin", "role": "inferred_view"}],
            "identity_views": {"status": "approved", "approved_reference_ids": ["views"],
                               "confirmed_by": "user", "note": "synthetic view confirmation"}}

    def save(self, profile=None, filename="one.json"):
        (self.root / filename).write_text(json.dumps(profile or self.profile))

    def test_name_alias_and_id_resolve_same_character_without_writes(self):
        self.save()
        before = (self.root / "one.json").read_bytes()
        for name in ("阿禾", " 禾禾 ", "person-001"):
            result = characters.resolve(self.root, name)
            self.assertEqual(result["id"], "person-001")
            self.assertEqual([r["id"] for r in result["optional_inferred_references"]], ["views"])
        self.assertEqual(before, (self.root / "one.json").read_bytes())

    def test_duplicate_alias_requires_selection(self):
        self.save()
        other = copy.deepcopy(self.profile)
        other.update(id="person-002", display_name="小禾")
        self.save(other, "two.json")
        with self.assertRaisesRegex(ValueError, "不唯一"):
            characters.resolve(self.root, "禾禾")

    def test_unknown_name_does_not_create_character(self):
        self.save()
        with self.assertRaisesRegex(ValueError, "未找到"):
            characters.resolve(self.root, "不存在")
        self.assertEqual(len(characters.catalog(self.root)), 1)

    def test_unconfirmed_image_name_or_views_cannot_be_reused(self):
        for field in ("review", "name_review", "identity_views"):
            with self.subTest(field=field):
                p = copy.deepcopy(self.profile)
                p[field]["confirmed_by"] = None
                self.save(p)
                with self.assertRaises(ValueError):
                    characters.resolve(self.root, "阿禾")

    def test_inferred_view_cannot_replace_real_primary(self):
        self.profile["master_reference_id"] = "views"
        self.save()
        with self.assertRaisesRegex(ValueError, "主图必须"):
            characters.resolve(self.root, "阿禾")

    def test_explicit_skip_allows_primary_only(self):
        self.profile["identity_views"].update(status="skipped_by_user", approved_reference_ids=[])
        self.save()
        result = characters.resolve(self.root, "阿禾")
        self.assertEqual(result["optional_inferred_references"], [])

    def test_missing_approved_view_is_reported(self):
        self.profile["identity_views"]["approved_reference_ids"] = ["rejected"]
        self.save()
        with self.assertRaisesRegex(ValueError, "文件缺失"):
            characters.resolve(self.root, "阿禾")

    def test_fictional_character_uses_own_confirmed_master(self):
        self.profile["kind"] = "fictional_character"
        self.profile["references"][0]["role"] = "approved_reference"
        self.save()
        self.assertEqual(characters.resolve(self.root, "阿禾")["master_reference"]["role"], "approved_reference")

    def test_rename_keeps_id_images_and_history(self):
        self.save()
        before = (self.root / "one.json").read_bytes()
        result = characters.rename(self.root, "person-001", "小舟", "测试用户要求改名并保留旧名", True)
        self.assertEqual(Path(result["history_path"]).read_bytes(), before)
        after = json.loads((self.root / "one.json").read_text())
        self.assertEqual(after["version"], 2)
        for field in ("id", "references", "review", "identity_views", "master_reference_id"):
            self.assertEqual(after[field], self.profile[field])
        for name in ("阿禾", "小舟", "禾禾", "person-001"):
            self.assertEqual(characters.resolve(self.root, name)["id"], "person-001")
        self.assertEqual(len(characters.catalog(self.root)), 1)

    def test_old_name_is_not_kept_without_request(self):
        self.save()
        characters.rename(self.root, "person-001", "小舟", "测试用户要求改名")
        with self.assertRaisesRegex(ValueError, "未找到"):
            characters.resolve(self.root, "阿禾")

    def test_confirm_proposed_name_does_not_approve_draft_appearance(self):
        self.profile.update(status="draft", review={"confirmed_by": None, "note": ""})
        self.profile["name_review"] = {"status": "proposed", "confirmed_by": None}
        self.save()
        characters.rename(self.root, "person-001", "阿禾", "测试用户认可暂定名")
        updated = json.loads((self.root / "one.json").read_text())
        self.assertEqual(updated["name_review"]["status"], "confirmed")
        with self.assertRaisesRegex(ValueError, "外貌尚未确认"):
            characters.resolve(self.root, "阿禾")

    def test_rename_conflict_is_normalized_and_does_not_mutate(self):
        self.save()
        other = copy.deepcopy(self.profile)
        other.update(id="person-002", display_name="Alice", aliases=[])
        self.save(other, "two.json")
        before = (self.root / "one.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "冲突"):
            characters.rename(self.root, "person-001", " ＡＬＩＣＥ ", "测试用户要求改名")
        self.assertEqual((self.root / "one.json").read_bytes(), before)
        self.assertFalse((self.root / "history").exists())

    def test_rename_requires_confirmation(self):
        self.save()
        with self.assertRaisesRegex(ValueError, "原话"):
            characters.rename(self.root, "person-001", "小舟", "")

    def test_history_collision_never_overwrites(self):
        self.save()
        history = self.root / "history/one/v0001.json"
        history.parent.mkdir(parents=True)
        history.write_text("existing historical evidence")
        before = (self.root / "one.json").read_bytes()
        with self.assertRaises(FileExistsError):
            characters.rename(self.root, "person-001", "小舟", "测试用户要求改名")
        self.assertEqual(history.read_text(), "existing historical evidence")
        self.assertEqual((self.root / "one.json").read_bytes(), before)
        self.assertFalse((self.root / ".rename.lock").exists())


if __name__ == "__main__":
    unittest.main()
