#!/usr/bin/env python3
"""Prepare and verify one age-change image request per character; no model calls or approvals."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import characters

require = characters.require
text = characters.text


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def age(value, label):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0, f"{label}需要已知的非负年龄，不能凭脸推断")
    return value


def target_age(operation, source_age):
    require(isinstance(operation, dict) and set(operation) in ({"years"}, {"target_age"}),
            "operation 只能指定 years（增长年数）或 target_age（目标年龄）之一")
    if "years" in operation:
        years = age(operation["years"], "增长年数")
        require(years > 0, "增长年数必须大于零")
        target = source_age + years
    else:
        target = age(operation["target_age"], "目标年龄")
    require(math.isfinite(target) and target > source_age, "目标年龄必须大于原年龄；逆龄请另行明确设计")
    return target


def source_subject(item, profiles, base):
    require(isinstance(item, dict) and text(item.get("name")), "每个角色需要名字或查询 ID")
    mode = item.get("source_mode", "approved_profile")
    if mode == "approved_profile":
        resolved = characters.resolve(profiles, item["name"])
        p = json.loads(Path(resolved["profile_path"]).read_text(encoding="utf-8"))
        ref = resolved["master_reference"]
        known_age = resolved.get("age_years")
        if known_age is not None and "source_age" in item:
            require(item["source_age"] == known_age, "年龄与档案不一致，请先确认所需年龄版本")
        source_age = item.get("source_age", known_age)
        if known_age is None:
            require(text(item.get("age_note")), "档案缺少年龄，需要用户提供的 source_age 和 age_note")
        subject = {"character_id": resolved["id"], "name": resolved["display_name"],
                   "source_version": resolved["version"], "profile_snapshot": p}
    elif mode == "selected_candidate":
        require(text(item.get("character_id")) and text(item.get("selection_note")),
                "本轮候选需要临时 ID 和用户指定该候选的说明；不等于正式建档认可")
        ref = item.get("reference")
        require(isinstance(ref, dict) and text(ref.get("path")), "本轮候选需要明确主图路径")
        ref = dict(ref, path=str((base / ref["path"]).resolve()))
        source_age = item.get("source_age")
        require(text(item.get("age_note")), "本轮候选需要原年龄依据 age_note")
        subject = {"character_id": item["character_id"], "name": item["name"], "source_version": None,
                   "selection_note": item["selection_note"]}
    else:
        raise ValueError("未知 source_mode；不自动把草稿当已认可档案")
    # Require explicit location even for a single-person photo, to prevent wrong-child selection.
    selector = item.get("selector", ref.get("selector"))
    require(text(selector), "需要 selector 指明参考图中的哪个人；单人照也请写明")
    path = Path(ref["path"])
    require(path.is_file() and path.stat().st_size > 0, "原年龄主图缺失或为空")
    source_age = age(source_age, "原年龄")
    identity_notes = item.get("identity_notes", [])
    require(isinstance(identity_notes, list) and all(text(n) for n in identity_notes), "identity_notes 应是可见特征列表")
    return dict(subject, source_mode=mode, source_age=source_age, age_note=item.get("age_note"),
                reference={"path": str(path.resolve()), "sha256": sha(path), "selector": selector},
                identity_notes=identity_notes)


def prompt(subject):
    before, after = subject["source_age"], subject["target_age"]
    notes = "; ".join(subject["identity_notes"]) or "Use the visible face in the source photograph as the identity anchor."
    return f"""Create one realistic everyday photograph of the SAME fictional age-progressed person.
Source person: {subject['reference']['selector']}.
Character label for bookkeeping: {subject['name']}. Source age: {before:g}; target age: approximately {after:g} years.
Show ONLY that person at the target age. Other people in the source are not additional facial references.
This is a speculative visual age progression, not a prediction of actual appearance.
Preserve identity-specific eyelid shape, eye spacing, brow-to-eye relationship, nose direction and tip character,
lip contour, smile-corner direction and characteristic facial relationships visible in this person.
Observed identity notes: {notes}
Change age through plausible overall facial development, cheek volume, facial length, jaw maturity,
neck and shoulder proportions appropriate to the target age. Preserve individuality through those changes.
Do not merely enlarge the original child or keep preschool proportions with an older hairstyle.
Do not redesign a new child by combining the parents. Do not copy adult facial hair, glasses or makeup from other people.
Age-appropriate ordinary casual clothing; a practical everyday haircut, low to moderate volume,
fine irregular strands, no sculpted ringlets, no fashionable tousled styling or salon blowout.
An attentive relaxed gaze with small natural catchlights, not enlarged eyes or a vacant mannequin expression.
Unretouched everyday-camera head-and-shoulders portrait, face unobscured, near-frontal angle,
gentle window daylight, simple neutral room, modest relaxed expression. No glamour styling,
beauty retouching, adult makeup on children, body emphasis, text, labels or collage.
"""


def prepare(spec_path, profiles, output):
    spec_path, output = Path(spec_path).resolve(), Path(output).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    require(isinstance(spec, dict) and spec.get("schema_version") == 1, "不支持的成长方案版本")
    items = spec.get("characters")
    require(isinstance(items, list) and items, "成长批次不能为空")
    subjects, ids = [], set()
    for item in items:
        subject = source_subject(item, profiles, spec_path.parent)
        require(subject["character_id"] not in ids, "同一批次重复指定了同一角色")
        ids.add(subject["character_id"])
        subject["target_age"] = target_age(spec.get("operation"), subject["source_age"])
        subjects.append(subject)
    require(not output.exists(), "输出目录已存在，请使用新目录保留旧实验")
    # Validate the whole batch before creating any output, so missing children cannot be silently skipped.
    output.mkdir(parents=True)
    files = {}
    for index, subject in enumerate(subjects, 1):
        folder = f"character-{index:02d}"
        path = output / folder
        path.mkdir()
        subject.update(request_path=f"{folder}/imagegen-request.json", expected_image=f"{folder}/result.png",
                       review_path=f"{folder}/review.json", status="prepared")
        request = {"prompt": prompt(subject), "referenced_image_paths": [subject["reference"]["path"]],
                   "transparent_background": False}
        dump(path / "imagegen-request.json", request)
        (path / "prompt.txt").write_text(request["prompt"], encoding="utf-8")
        dump(path / "stage-candidate.json", {"schema_version": 1, "character_id": subject["character_id"],
             "display_name": subject["name"], "source_version": subject["source_version"],
             "source_age": subject["source_age"], "age_years": subject["target_age"], "status": "draft",
             "image_path": "result.png", "review": {"confirmed_by": None, "note": ""}})
        dump(path / "review.json", {"generation_status": "not_run", "image_sha256": None,
             "identity_continuity": None, "apparent_age": None, "ordinary_styling": None,
             "non_target_drift": [], "user_feedback": None, "accepted": False})
        for name in ("imagegen-request.json", "prompt.txt", "stage-candidate.json"):
            files[f"{folder}/{name}"] = sha(path / name)
    manifest = {"schema_version": 1, "task": "age_progression", "status": "prepared_not_generated",
                "spec_snapshot": spec, "subjects": subjects, "immutable_files": files,
                "note": "请求准备不是生图完成；逐个执行、展示名字和年龄，并等待用户选择。原档案未修改。"}
    dump(output / "run.json", manifest)
    return {"run_path": str(output / "run.json"), "count": len(subjects), "status": manifest["status"],
            "characters": [{k: s[k] for k in ("character_id", "name", "source_age", "target_age", "request_path")} for s in subjects]}


def verify(run_path):
    run_path = Path(run_path).resolve()
    root = run_path.parent
    run = json.loads(run_path.read_text(encoding="utf-8"))
    require(run.get("schema_version") == 1 and run.get("task") == "age_progression", "不是成长运行记录")
    subjects = run.get("subjects", [])
    require(isinstance(subjects, list) and subjects, "缺少角色清单")
    require(len(subjects) == len(run["spec_snapshot"]["characters"]), "批次数量不一致")
    require(len({s["character_id"] for s in subjects}) == len(subjects), "运行清单中有重复角色")
    expected_files = set()
    for index, subject in enumerate(subjects, 1):
        folder = f"character-{index:02d}"
        expected_files.update(f"{folder}/{n}" for n in ("imagegen-request.json", "prompt.txt", "stage-candidate.json"))
        require(subject["request_path"] == f"{folder}/imagegen-request.json", "角色请求路径不一致")
        require(subject["target_age"] == target_age(run["spec_snapshot"]["operation"], age(subject["source_age"], "原年龄")), "年龄计算不一致")
        ref = subject["reference"]
        require(Path(ref["path"]).is_file() and sha(ref["path"]) == ref["sha256"], "原年龄参考文件变化或缺失")
        request = json.loads((root / subject["request_path"]).read_text(encoding="utf-8"))
        require(request == {"prompt": prompt(subject), "referenced_image_paths": [ref["path"]], "transparent_background": False},
                "请求与角色、年龄或参考清单不一致")
    require(set(run.get("immutable_files", {})) == expected_files, "缺少准备文件的校验清单")
    for rel, digest in run["immutable_files"].items():
        path = (root / rel).resolve()
        require(path.is_relative_to(root) and path.is_file() and sha(path) == digest, "准备文件变化或缺失: " + rel)
    return {"valid": True, "count": len(subjects), "note": "仅校验准备文件与输入完整性；不证明图片生成、相似度或用户认可。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--spec", required=True)
    prep.add_argument("--profiles", default="portrait-data/profiles")
    prep.add_argument("--output", required=True)
    check = sub.add_parser("verify")
    check.add_argument("run")
    args = parser.parse_args()
    try:
        result = prepare(args.spec, args.profiles, args.output) if args.command == "prepare" else verify(args.run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
        parser.exit(2, f"错误: {exc}\n")


if __name__ == "__main__":
    main()
