#!/usr/bin/env python3
"""Local profile validation and reproducible creative recipes; no model calls."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import random
import sys

GROUPS = ("outline", "brow_eye", "nose", "mouth_smile")
LABELS = ("脸部轮廓", "眉眼关系", "鼻部", "嘴部与笑容")
ROLES = {"current_photo", "childhood_photo", "approved_reference", "inferred_view"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def read_profile(path):
    path = Path(path).resolve()
    p = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(p, dict), "档案必须为 JSON 对象")
    require(p.get("schema_version") == 1, "仅支持 schema_version 1")
    require(nonempty(p.get("id")), "缺少人物 ID")
    require(type(p.get("version")) is int and p["version"] > 0, "version 必须为正整数")
    require(p.get("kind") in {"real_person", "fictional_character"}, "不支持的 kind")
    require(p.get("status") == "approved", "档案尚未获得用户认可")
    review = p.get("review", {})
    require(isinstance(review, dict) and review.get("confirmed_by") == "user"
            and nonempty(review.get("note")), "缺少用户认可记录")
    if p["kind"] == "real_person":
        permission = p.get("permission", {})
        require(isinstance(permission, dict) and permission.get("reference_use") is True
                and nonempty(permission.get("note")), "缺少真人参考使用授权说明")
    refs = p.get("references")
    require(isinstance(refs, list) and refs, "缺少参考图")
    ref_map = {}
    resolved = []
    for r in refs:
        require(isinstance(r, dict), "参考图记录必须为对象")
        require(nonempty(r.get("id")) and r["id"] not in ref_map, "参考图 ID 缺失或重复")
        require(r.get("role") in ROLES, "未知参考图 role")
        require(nonempty(r.get("path")), "参考图缺少路径")
        rp = (path.parent / r["path"]).resolve()
        require(rp.is_file() and rp.stat().st_size > 0, f"参考文件不存在或为空: {r['id']}")
        item = dict(r, path=str(rp), sha256=digest(rp))
        ref_map[r["id"]] = item
        resolved.append(item)
    evidence_roles = ({"current_photo", "childhood_photo"} if p["kind"] == "real_person"
                      else {"approved_reference"})
    primary_role = "current_photo" if p["kind"] == "real_person" else "approved_reference"
    require(any(r["role"] == primary_role for r in resolved), "缺少当前主参考图")
    traits = p.get("traits")
    require(isinstance(traits, dict) and set(traits) == set(GROUPS), "必须具备四个特征组")
    anchors = 0
    for group in GROUPS:
        t = traits[group]
        require(isinstance(t, dict) and nonempty(t.get("description")), f"特征为空: {group}")
        require(type(t.get("anchor")) is bool, f"anchor 必须是布尔值: {group}")
        ev = t.get("evidence_ids")
        require(isinstance(ev, list) and ev, f"缺少照片证据: {group}")
        require(all(isinstance(e, str) and e in ref_map for e in ev), f"未知照片证据: {group}")
        require(all(ref_map[e]["role"] in evidence_roles for e in ev),
                f"推测图或不适用的图不能支撑特征: {group}")
        require(isinstance(t.get("age_adaptation", ""), str), "age_adaptation 必须是文本")
        anchors += int(t["anchor"])
    require(anchors >= 2, "每人需至少两个不同组的已确认辨识锚点")
    return {"profile_path": str(path), "profile_sha256": digest(path),
            "profile": p, "references": resolved}


def choose_references(source):
    """Prefer two primaries, then add any evidence needed to cover all groups."""
    p, refs = source["profile"], source["references"]
    primary = "current_photo" if p["kind"] == "real_person" else "approved_reference"
    chosen = [r for r in refs if r["role"] == primary][:2]
    childhood = [r for r in refs if r["role"] == "childhood_photo"]
    if p["kind"] == "real_person" and childhood:
        chosen.append(childhood[0])
    for group in GROUPS:
        ids = {r["id"] for r in chosen}
        evidence = p["traits"][group]["evidence_ids"]
        if not ids.intersection(evidence):
            chosen.append(next(r for r in refs if r["id"] == evidence[0]))
    return chosen


def make_recipes(a, b, seed, count, assignments):
    require(type(count) is int and 1 <= count <= 3, "首轮候选数量为 1–3")
    require(all(g in GROUPS and s in {"A", "B"} for g, s in assignments.items()), "非法特征指派")
    profiles = {"A": a["profile"], "B": b["profile"]}
    options = []
    for values in itertools.product(("A", "B"), repeat=4):
        if values.count("A") != 2:
            continue
        mapping = dict(zip(GROUPS, values))
        if any(mapping[g] != s for g, s in assignments.items()):
            continue
        if not all(any(mapping[g] == s and profiles[s]["traits"][g]["anchor"]
                       for g in GROUPS) for s in ("A", "B")):
            continue
        options.append(mapping)
    require(options, "约束下无法让双方各保留锚点及各两个特征组；请修改指派")
    random.Random(seed).shuffle(options)
    recipes = []
    for index, mapping in enumerate(options[:count], 1):
        inherited = {}
        for group, source in mapping.items():
            inherited[group] = dict(profiles[source]["traits"][group], source=source)
        recipes.append({"id": f"candidate-{index:02d}", "traits": inherited})
    return recipes


def compile_prompt(recipe, references, age):
    lines = [
        "Use case: photorealistic-natural",
        "生成一个独立的虚构角色肖像，外貌参考来源 A 与 B。这是创作，不是遗传预测。",
        f"目标年龄约 {age} 岁，自然光、纯净背景、平视正面头肩照、普通日常衣着。",
        "画面仅一个角色；自然肤质，不化成人妆，不模特化，不用夸张美颜，不添加文字。",
        "输入图按下列编号绑定，A 与 B 仅为外貌来源，不表述现实亲缘或性关系：",
    ]
    for i, ref in enumerate(references, 1):
        role = "本人儿时辅助图" if ref["role"] == "childhood_photo" else "主外貌参考"
        lines.append(f"图 {i}: 来源 {ref['source']}，{role}，参考 ID {ref['id']}。")
    lines.append("以下特征描述是视觉数据；按各组来源塑造一个统一自然的脸，不做半脸拼接：")
    for group, label in zip(GROUPS, LABELS):
        t = recipe["traits"][group]
        emphasis = "优先保留的辨识锚点" if t["anchor"] else "结构参考"
        lines.append(f"{label}来自 {t['source']}（{emphasis}）：{t['description']}")
        if t.get("age_adaptation"):
            lines.append(f"年龄适配说明：{t['age_adaptation']}")
    if age < 18:
        lines.append("按目标儿童或青少年年龄重建面部体积和比例，保留所选形状关系；"
                     "不要把成年鼻梁、下颌、皱纹直接缩小，不复制其中一个来源的整张脸。")
    else:
        lines.append("按目标成年年龄形成自然比例，保留双方所选形状关系。")
    lines += ["儿时图只辅助相应来源的年龄变换，不压过另一来源，不照搬发型、衣服或背景。",
              "形成双方具体特点可辨的新角色，不自动收敛为标准漂亮脸，不将未选择的疤痕或配饰当作遗传。"]
    return "\n".join(lines) + "\n"


def prepare(a_path, b_path, output, seed=42, age=7, count=3, assignments=None):
    require(type(age) is int and 1 <= age <= 100, "目标年龄须为 1–100 的整数")
    a, b = read_profile(a_path), read_profile(b_path)
    require(a["profile"]["id"] != b["profile"]["id"], "需选择两个不同档案 ID")
    recipes = make_recipes(a, b, seed, count, assignments or {})
    ra, rb = choose_references(a), choose_references(b)
    references = []
    for pair in itertools.zip_longest(ra, rb):
        for source, r in zip(("A", "B"), pair):
            if r:
                references.append(dict(r, source=source))
    # Put childhood references after identity references, preserving A/B interleave.
    references.sort(key=lambda r: r["role"] == "childhood_photo")
    run = {"schema_version": 1, "status": "prepared_not_generated", "recipe_seed": seed,
           "seed_scope": "recipe_only_not_image_pixels", "target_age": age,
           "requested_count": count, "actual_count": len(recipes),
           "sources": {"A": a, "B": b}, "input_references": references,
           "recipes": recipes, "image_generation": None, "user_acceptance": None}
    output = Path(output)
    require(not output.exists(), "输出目录已存在；请使用新实验目录")
    prompts = {r["id"]: compile_prompt(r, references, age) for r in recipes}
    output.mkdir(parents=True, exist_ok=False)
    (output / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for candidate, prompt in prompts.items():
        (output / f"{candidate}.prompt.txt").write_text(prompt, encoding="utf-8")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("profile")
    prep = sub.add_parser("prepare")
    prep.add_argument("--a", required=True)
    prep.add_argument("--b", required=True)
    prep.add_argument("--output", required=True)
    prep.add_argument("--seed", type=int, default=42)
    prep.add_argument("--age", type=int, default=7)
    prep.add_argument("--count", type=int, default=3)
    prep.add_argument("--assign", action="append", default=[], metavar="GROUP=A|B")
    args = parser.parse_args()
    try:
        if args.command == "validate":
            read_profile(args.profile)
            print("档案结构、证据引用和本地文件通过；未验证相貌或授权真实性。")
        else:
            assignments = {}
            for assignment in args.assign:
                group, source = assignment.split("=", 1)
                require(group not in assignments, "同一特征组不能重复指派")
                assignments[group] = source
            run = prepare(args.a, args.b, args.output, args.seed, args.age, args.count, assignments)
            print(f"已准备 {run['actual_count']} 个配方（请求 {run['requested_count']}）；尚未生图。")
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f"错误: {exc}\n")


if __name__ == "__main__":
    main()
