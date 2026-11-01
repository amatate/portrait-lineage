"""Check provenance, source balance, reproducibility and safe local persistence."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "skills/portrait-lineage/scripts/lineage.py"
spec = importlib.util.spec_from_file_location("lineage", MODULE)
lineage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lineage)


class LineageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Opaque fixture bytes test file bookkeeping only, never image generation.
        (self.root / "reference.bin").write_bytes(b"synthetic file fixture; not a photo")
        self.base = {
            "schema_version": 1, "id": "a", "version": 1, "kind": "real_person",
            "status": "approved", "review": {"confirmed_by": "user", "note": "test fixture"},
            "permission": {"reference_use": True, "note": "test fixture"},
            "references": [{"id": "r", "path": "reference.bin", "role": "current_photo"}],
            "traits": {g: {"description": f"fictional {g}", "anchor": True,
                           "evidence_ids": ["r"], "age_adaptation": ""} for g in lineage.GROUPS}
        }

    def save(self, p, name="a.json"):
        path = self.root / name
        path.write_text(json.dumps(p), encoding="utf-8")
        return path

    def pair(self):
        b = copy.deepcopy(self.base)
        b["id"] = "b"
        return self.save(self.base), self.save(b, "b.json")

    def test_draft_cannot_enter_recipe(self):
        self.base["status"] = "draft"
        with self.assertRaises(ValueError):
            lineage.read_profile(self.save(self.base))

    def test_assistant_cannot_sign_user_acceptance(self):
        self.base["review"]["confirmed_by"] = "assistant"
        with self.assertRaises(ValueError):
            lineage.read_profile(self.save(self.base))

    def test_missing_permission_rejected(self):
        self.base["permission"]["reference_use"] = False
        with self.assertRaises(ValueError):
            lineage.read_profile(self.save(self.base))

    def test_generated_view_cannot_support_real_feature(self):
        self.base["references"].append({"id": "v", "path": "reference.bin", "role": "inferred_view"})
        self.base["traits"]["nose"]["evidence_ids"] = ["v"]
        with self.assertRaises(ValueError):
            lineage.read_profile(self.save(self.base))

    def test_missing_file_rejected(self):
        self.base["references"][0]["path"] = "missing.jpg"
        with self.assertRaises(ValueError):
            lineage.read_profile(self.save(self.base))

    def test_fictional_character_reusable(self):
        self.base["kind"] = "fictional_character"
        self.base["references"][0]["role"] = "approved_reference"
        del self.base["permission"]
        self.assertEqual(lineage.read_profile(self.save(self.base))["profile"]["kind"], "fictional_character")

    def test_same_seed_reproduces_recipe_not_generated_image(self):
        a, b = self.pair()
        first = lineage.prepare(a, b, self.root / "run1", seed=7)
        second = lineage.prepare(a, b, self.root / "run2", seed=7)
        self.assertEqual(first, second)
        self.assertIsNone(first["image_generation"])
        self.assertIsNone(first["user_acceptance"])
        self.assertEqual(len(first["recipes"]), 3)
        for recipe in first["recipes"]:
            sources = [t["source"] for t in recipe["traits"].values()]
            self.assertEqual(sources.count("A"), 2)
            self.assertEqual(sources.count("B"), 2)

    def test_both_sources_retain_real_selected_anchors(self):
        for g in ("nose", "mouth_smile"):
            self.base["traits"][g]["anchor"] = False
        a, b = self.pair()
        for seed in range(20):
            recipes = lineage.make_recipes(lineage.read_profile(a), lineage.read_profile(b), seed, 3, {})
            for recipe in recipes:
                for source in ("A", "B"):
                    self.assertTrue(any(t["source"] == source and t["anchor"] for t in recipe["traits"].values()))

    def test_reference_added_when_primary_limit_would_drop_evidence(self):
        for i in (2, 3):
            self.base["references"].append({"id": f"r{i}", "path": "reference.bin", "role": "current_photo"})
        self.base["traits"]["nose"]["evidence_ids"] = ["r3"]
        refs = lineage.choose_references(lineage.read_profile(self.save(self.base)))
        self.assertIn("r3", {r["id"] for r in refs})

    def test_user_assignments_respected_and_conflicts_rejected(self):
        a, b = self.pair()
        run = lineage.prepare(a, b, self.root / "run", assignments={"brow_eye": "A", "nose": "B"})
        for recipe in run["recipes"]:
            self.assertEqual(recipe["traits"]["brow_eye"]["source"], "A")
            self.assertEqual(recipe["traits"]["nose"]["source"], "B")
        with self.assertRaises(ValueError):
            lineage.prepare(a, b, self.root / "bad", assignments={g: "A" for g in lineage.GROUPS})
        self.assertFalse((self.root / "bad").exists())

    def test_existing_run_never_overwritten(self):
        a, b = self.pair()
        lineage.prepare(a, b, self.root / "run")
        snapshot = (self.root / "run/run.json").read_bytes()
        with self.assertRaises(ValueError):
            lineage.prepare(a, b, self.root / "run", seed=2)
        self.assertEqual(snapshot, (self.root / "run/run.json").read_bytes())

    def test_reference_file_changes_are_visible_in_provenance(self):
        path = self.save(self.base)
        first = lineage.read_profile(path)
        (self.root / "reference.bin").write_bytes(b"different fixture")
        second = lineage.read_profile(path)
        self.assertNotEqual(first["references"][0]["sha256"], second["references"][0]["sha256"])


if __name__ == "__main__":
    unittest.main()
