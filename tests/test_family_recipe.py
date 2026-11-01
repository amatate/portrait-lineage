import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "skills/portrait-lineage/scripts/family_recipe.py"
spec = importlib.util.spec_from_file_location("family_recipe", MODULE)
family = importlib.util.module_from_spec(spec)
spec.loader.exec_module(family)


class FamilyRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "b.bin").write_bytes(b"test bytes, not an image")
        self.registry = {"references": [
            {"id": "B_adults", "family_id": "B", "path": "b.bin", "sha256": family.sha(self.root / "b.bin"), "role": "adult_identity"},
            {"id": "A_adults", "family_id": "A", "path": "b.bin", "sha256": family.sha(self.root / "b.bin"), "role": "adult_identity"}
        ]}
        self.study = {
            "schema_version": 1, "family_id": "B", "status": "observed_draft", "user_acceptance": None,
            "source_reference_ids": ["B_adults"],
            "people": [{"id": pid, "reference_id": "B_adults", "selector": side,
                        "identity_lock": "test face traits", "uncertain_features": ["partly hidden brows"]}
                       for pid, side in (("P1", "left adult"), ("P2", "right adult"))],
            "children": [{"id": cid, "age": age, "presentation": title,
                          "traits": [{"source_person_id": pid, "module": module, "observed_cue": "test cue",
                                      "child_translation": "test age adaptation"}
                                     for pid, module in (("P1", "outline"), ("P2", "mouth"))]}
                         for cid, age, title in (("boy", 6, "男孩"), ("girl", 4, "女孩"))],
            "sibling_shared_trait": "nose tip", "sibling_difference": "different face length",
            "scene": "cafe", "composition": "four clear faces", "test_objective": "family likeness"
        }

    def write(self):
        family.dump(self.root / "registry.json", self.registry)
        family.dump(self.root / "study.json", self.study)
        return self.root / "registry.json", self.root / "study.json"

    def prepare(self):
        return family.prepare(*self.write(), self.root / "run")

    def use_visual_study(self):
        self.study["guidance_mode"] = "visual_first"
        for child in self.study["children"]:
            child.pop("traits")
            child["reference_people"] = ["P1", "P2"]
            child["visual_brief"] = "A whole-face direction based on adult reference only."
        (self.root / "child.bin").write_bytes(b"a distinct childhood fixture")
        self.registry["references"].append({"id": "B_childhood", "family_id": "B", "path": "child.bin",
            "sha256": family.sha(self.root / "child.bin"), "role": "childhood_proportions"})

    def prepare_pair(self):
        return family.prepare_ablation(*self.write(), self.root / "pair", "B_childhood")

    def test_children_only_keeps_source_bindings_without_drawing_adults(self):
        self.use_visual_study()
        self.study["output_subjects"] = "children_only"
        self.study["composition"] = "two children, faces clear"
        run = self.prepare()
        request = family.verify_run(self.root / "run/run.json")
        self.assertEqual(request["referenced_image_paths"], [str((self.root / "b.bin").resolve())])
        self.assertEqual(len(run["spec_snapshot"]["people"]), 2)
        self.assertIn("成人只作来源参考，不出现在画面中", request["prompt"])
        self.assertNotIn("四人", request["prompt"])
        self.assertNotIn("四脸", request["prompt"])

    def test_invalid_output_scope_does_not_silently_make_family(self):
        self.use_visual_study()
        self.study["output_subjects"] = "unsupported"
        with self.assertRaisesRegex(ValueError, "未知输出人物范围"):
            self.prepare()

    def test_legacy_recipe_cannot_silently_ignore_children_only(self):
        self.study["output_subjects"] = "children_only"
        with self.assertRaisesRegex(ValueError, "需使用视觉优先"):
            self.prepare()

    def use_linked_study(self):
        self.use_visual_study()
        (self.root / "extra.bin").write_bytes(b"additional source reference fixture")
        self.registry["references"].append({"id": "B_color", "family_id": "B", "path": "extra.bin",
            "sha256": family.sha(self.root / "extra.bin"), "role": "adult_identity"})
        self.study["source_reference_ids"].append("B_color")
        self.study["reference_usage"] = [{"reference_id": "B_color", "person_ids": ["P2"],
            "selector": "the single adult", "purpose": "smile-shape evidence", "limitations": ["flash alters color"]}]
        self.study["scene_mode"] = "face_comparison"
        for child in self.study["children"]:
            child["facial_links"] = [
                {"source_person_id": pid, "reference_id": rid, "region": region,
                 "observed_cue": f"{child['id']}-{pid}-visible-cue", "age_adaptation": "reduce mature projection",
                 "check_target": "compare visible geometry without relying on hair"}
                for pid, rid, region in (("P1", "B_adults", "brow_eye"), ("P2", "B_color", "mouth_smile"))]

    def test_per_child_links_and_photo_duties_reach_actual_request(self):
        self.use_linked_study()
        run = self.prepare()
        request = family.verify_run(self.root / "run/run.json")
        for child in self.study["children"]:
            for link in child["facial_links"]:
                self.assertIn(link["observed_cue"], request["prompt"])
        self.assertIn("flash alters color", request["prompt"])
        self.assertEqual(run["spec_snapshot"]["children"], self.study["children"])
        self.assertIsNone(run["user_acceptance"])

    def test_each_child_link_table_must_cover_both_people(self):
        self.use_linked_study()
        self.study["children"][0]["facial_links"] = self.study["children"][0]["facial_links"][:1]
        with self.assertRaisesRegex(ValueError, "覆盖双方"):
            self.prepare()

    def test_extra_reference_cannot_support_wrong_person(self):
        self.use_linked_study()
        self.study["children"][0]["facial_links"][0]["reference_id"] = "B_color"
        with self.assertRaisesRegex(ValueError, "未绑定该人物"):
            self.prepare()

    def test_reference_duty_cannot_include_unselected_photo(self):
        self.use_linked_study()
        self.study["reference_usage"][0]["reference_id"] = "A_adults"
        with self.assertRaisesRegex(ValueError, "未选照片"):
            self.prepare()

    def test_linked_ablation_keeps_all_other_inputs_identical(self):
        self.use_linked_study()
        self.prepare_pair()
        a, b = family.verify_pair(self.root / "pair/pair.json")
        self.assertEqual(a["prompt"], b["prompt"])
        self.assertEqual(a["referenced_image_paths"], b["referenced_image_paths"][:-1])
        self.assertEqual(len(a["referenced_image_paths"]), 2)

    def test_unknown_scene_mode_is_not_silently_ignored(self):
        self.use_linked_study()
        self.study["scene_mode"] = "unsupported"
        with self.assertRaisesRegex(ValueError, "照片用途"):
            self.prepare()

    def test_group_photo_binds_two_people_once_without_fake_approval(self):
        run = self.prepare()
        request = family.verify_run(self.root / "run/run.json")
        self.assertEqual(len(request["referenced_image_paths"]), 1)
        self.assertIn("P1 = 输入图 1", request["prompt"])
        self.assertIn("P2 = 输入图 1", request["prompt"])
        self.assertEqual([c["age"] for c in run["spec_snapshot"]["children"]], [6, 4])
        self.assertIsNone(run["user_acceptance"])

    def test_other_family_is_rejected(self):
        self.study["source_reference_ids"].append("A_adults")
        with self.assertRaisesRegex(ValueError, "其他家组"):
            self.prepare()

    def test_generated_candidate_is_rejected(self):
        self.registry["references"][0]["role"] = "generated_candidate"
        with self.assertRaises(ValueError):
            self.prepare()

    def test_image_substitution_before_prepare_is_rejected(self):
        (self.root / "b.bin").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.prepare()

    def test_image_substitution_after_prepare_is_rejected(self):
        self.prepare()
        (self.root / "b.bin").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            family.verify_run(self.root / "run/run.json")

    def test_prompt_substitution_is_rejected(self):
        self.prepare()
        (self.root / "run/prompt.txt").write_text("different prompt")
        with self.assertRaises(ValueError):
            family.verify_run(self.root / "run/run.json")

    def test_two_adults_cannot_point_to_same_subject(self):
        self.study["people"][1]["selector"] = "left adult"
        with self.assertRaises(ValueError):
            self.prepare()

    def test_each_child_needs_both_sources(self):
        self.study["children"][0]["traits"][1]["source_person_id"] = "P1"
        with self.assertRaises(ValueError):
            self.prepare()

    def test_same_file_aliases_are_deduplicated_and_bound(self):
        alias = copy.deepcopy(self.registry["references"][0])
        alias["id"] = "B_alias"
        self.registry["references"].append(alias)
        self.study["source_reference_ids"].extend(["B_alias", "B_adults"])
        self.study["people"][1]["reference_id"] = "B_alias"
        self.prepare()
        request = family.verify_run(self.root / "run/run.json")
        self.assertEqual(len(request["referenced_image_paths"]), 1)
        self.assertIn("P2 = 输入图 1", request["prompt"])

    def test_user_acceptance_cannot_be_fabricated_by_study(self):
        self.study["user_acceptance"] = True
        with self.assertRaises(ValueError):
            self.prepare()

    def test_existing_run_remains_unchanged(self):
        self.prepare()
        digest = family.sha(self.root / "run/run.json")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(digest, family.sha(self.root / "run/run.json"))

    def test_visual_mode_runs_without_part_assignment(self):
        self.use_visual_study()
        run = self.prepare()
        request = family.verify_run(self.root / "run/run.json")
        self.assertTrue(request["prompt"])
        self.assertNotIn("traits", run["spec_snapshot"]["children"][0])
        self.assertIsNone(run["user_acceptance"])

    def test_visual_mode_still_requires_both_people(self):
        self.use_visual_study()
        self.study["children"][0]["reference_people"] = ["P1"]
        with self.assertRaises(ValueError):
            self.prepare()

    def test_visual_mode_rejects_stacked_part_recipes(self):
        self.use_visual_study()
        self.study["children"][0]["traits"] = [{"source_person_id": "P1"}]
        with self.assertRaises(ValueError):
            self.prepare()

    def test_ablation_changes_only_one_input_image(self):
        self.use_visual_study()
        self.prepare_pair()
        a, b = family.verify_pair(self.root / "pair/pair.json")
        self.assertEqual(a["prompt"], b["prompt"])
        self.assertEqual(a["transparent_background"], b["transparent_background"])
        self.assertEqual(a["referenced_image_paths"], b["referenced_image_paths"][:-1])
        self.assertEqual(len(b["referenced_image_paths"]), len(a["referenced_image_paths"]) + 1)
        self.assertIsNone(family.read(self.root / "pair/pair.json")["user_acceptance"])

    def test_ablation_rejects_childhood_from_other_family(self):
        self.use_visual_study()
        self.registry["references"][-1]["family_id"] = "A"
        with self.assertRaises(ValueError):
            self.prepare_pair()
        self.assertFalse((self.root / "pair").exists())

    def test_ablation_rejects_already_present_childhood_input(self):
        self.use_visual_study()
        self.study["source_reference_ids"].append("B_childhood")
        with self.assertRaises(ValueError):
            self.prepare_pair()

    def test_pair_rejects_different_prompt_despite_valid_individual_run(self):
        self.use_visual_study()
        self.prepare_pair()
        condition = self.root / "pair/with_childhood"
        request = family.read(condition / "imagegen-request.json")
        request["prompt"] += "Change scene and clothing."
        family.dump(condition / "imagegen-request.json", request)
        (condition / "prompt.txt").write_text(request["prompt"], encoding="utf-8")
        run = family.read(condition / "run.json")
        run["request_sha256"] = family.sha(condition / "imagegen-request.json")
        run["prompt_sha256"] = family.sha(condition / "prompt.txt")
        family.dump(condition / "run.json", run)
        family.verify_run(condition / "run.json")
        with self.assertRaises(ValueError):
            family.verify_pair(self.root / "pair/pair.json")

    def test_ablation_does_not_overwrite_a_prior_pair(self):
        self.use_visual_study()
        self.prepare_pair()
        before = family.sha(self.root / "pair/pair.json")
        with self.assertRaises(ValueError):
            self.prepare_pair()
        self.assertEqual(before, family.sha(self.root / "pair/pair.json"))


if __name__ == "__main__":
    unittest.main()
