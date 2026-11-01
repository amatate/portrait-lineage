#!/usr/bin/env python3
"""Prepare one family portrait study from a scoped, hash-verified registry."""
import argparse
import copy
import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def present(value):
    return isinstance(value, str) and bool(value.strip())


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate(registry_path, spec_path, spec_override=None):
    registry_path, spec_path = Path(registry_path).resolve(), Path(spec_path).resolve()
    registry = read(registry_path)
    spec = copy.deepcopy(spec_override) if spec_override is not None else read(spec_path)
    require(spec.get("schema_version") == 1, "仅支持方案版本 1")
    require(spec.get("status") == "observed_draft" and spec.get("user_acceptance") is None,
            "实验使用未认可的观察草稿，不自动签署用户认可")
    require(present(spec.get("family_id")), "缺少家组 ID")
    records = registry.get("references", [])
    require(isinstance(records, list) and records, "登记清单为空")
    refs = {}
    for item in records:
        require(present(item.get("id")) and item["id"] not in refs, "登记清单含重复或空 ID")
        refs[item["id"]] = item
    selected = spec.get("source_reference_ids")
    require(isinstance(selected, list) and selected and all(isinstance(x, str) for x in selected), "缺少输入清单")
    resolved, selected_by_id = [], {}
    unique_paths = {}
    for rid in dict.fromkeys(selected):
        require(rid in refs, "输入未在登记清单中")
        ref = refs[rid]
        require(ref.get("family_id") == spec["family_id"], "输入混入其他家组")
        require(ref.get("role") in {"adult_identity", "childhood_proportions"}, "本入口不接受生成候选作为原始参考")
        require(present(ref.get("path")), "输入缺少路径")
        path = (registry_path.parent / ref["path"]).resolve()
        require(path.is_file() and sha(path) == ref.get("sha256"), "输入文件缺失或哈希不一致")
        resolved_ref = dict(ref, path=str(path))
        selected_by_id[rid] = resolved_ref
        if str(path) not in unique_paths:
            resolved_ref["aliases"] = [rid]
            resolved.append(resolved_ref)
            unique_paths[str(path)] = resolved_ref
        else:
            require(unique_paths[str(path)]["role"] == ref["role"], "同一原图不能伪装成不同职责")
            unique_paths[str(path)]["aliases"].append(rid)
    people = spec.get("people", [])
    require(len(people) == 2 and {p.get("id") for p in people} == {"P1", "P2"}, "需要 P1/P2 两位成人")
    selectors = set()
    for p in people:
        require(p.get("reference_id") in selected_by_id, "成人没有绑定本次输入")
        require(selected_by_id[p["reference_id"]]["role"] == "adult_identity", "成人需绑定成人原始参考")
        require(present(p.get("selector")) and present(p.get("identity_lock")), "缺少图内定位或成人保持描述")
        identity = (selected_by_id[p["reference_id"]]["path"], p["selector"])
        require(identity not in selectors, "两位成人不能使用同一个图内定位")
        selectors.add(identity)
        require(isinstance(p.get("uncertain_features"), list)
                and all(present(x) for x in p["uncertain_features"]), "不确定项须为文本列表")
    # A source photo may support one person or a positioned group. Extra photos
    # must be assigned explicitly instead of becoming interchangeable evidence.
    bindings = {(p["id"], selected_by_id[p["reference_id"]]["path"]) for p in people}
    usage = spec.get("reference_usage", [])
    require(isinstance(usage, list), "参考分工须为列表")
    for item in usage:
        require(isinstance(item, dict) and item.get("reference_id") in selected_by_id, "参考分工引用了本轮未选照片")
        subjects = item.get("person_ids")
        require(isinstance(subjects, list) and all(pid in {"P1", "P2"} for pid in subjects), "参考分工人物无效")
        ref = selected_by_id[item["reference_id"]]
        require(ref["role"] != "adult_identity" or bool(subjects), "成人参考分工缺少人物")
        require(all(present(item.get(k)) for k in ("selector", "purpose")), "参考分工缺少定位或用途")
        require(isinstance(item.get("limitations"), list)
                and all(present(x) for x in item["limitations"]), "参考限制须为文本列表")
        bindings.update((pid, ref["path"]) for pid in subjects)
    children = spec.get("children", [])
    require(len(children) == 2 and all(present(c.get("id")) for c in children)
            and len({c["id"] for c in children}) == 2, "需要两个独立孩子")
    mode = spec.get("guidance_mode", "feature_recipe")
    require(mode in {"feature_recipe", "visual_first"}, "未知构思模式")
    subjects = spec.get("output_subjects", "family")
    require(subjects in {"family", "children_only"}, "未知输出人物范围")
    require(subjects == "family" or mode == "visual_first", "仅儿童试片需使用视觉优先模式")
    require(spec.get("scene_mode", "face_comparison") in {"face_comparison", "candid_lifestyle"}, "未知照片用途")
    if mode != "visual_first":
        require(not usage and "scene_mode" not in spec, "参考分工和照片用途需使用视觉优先模式")
    for c in children:
        require(type(c.get("age")) is int and 1 <= c["age"] <= 17, "儿童年龄需为 1–17")
        require(present(c.get("presentation")), "缺少角色呈现")
        if mode == "visual_first":
            require(present(c.get("visual_brief")), "缺少儿童整体视觉构思")
            sources = c.get("reference_people")
            require(isinstance(sources, list) and set(sources) == {"P1", "P2"}, "每名孩子须参考双方整体外貌")
            require(not c.get("traits"), "视觉优先模式不叠加逐部位配方；请使用 visual_brief")
            if "facial_links" in c:
                links = c["facial_links"]
                require(isinstance(links, list) and links and all(isinstance(x, dict) for x in links), "五官核对表不能为空")
                require({link.get("source_person_id") for link in links} == {"P1", "P2"}, "每名孩子的五官核对表须覆盖双方")
                for link in links:
                    rid = link.get("reference_id")
                    require(rid in selected_by_id and selected_by_id[rid]["role"] == "adult_identity", "五官线索需绑定本轮成人原图")
                    require((link["source_person_id"], selected_by_id[rid]["path"]) in bindings, "五官线索引用了未绑定该人物的照片")
                    require(link.get("region") in {"brow_eye", "nose", "mouth_smile", "outline"}, "五官核对部位无效")
                    require(all(present(link.get(k)) for k in ("observed_cue", "age_adaptation", "check_target")), "五官核对表缺少观察、年龄适配或检查目标")
        else:
            require("facial_links" not in c, "五官核对表需使用视觉优先模式")
            traits = c.get("traits", [])
            require(isinstance(traits, list) and traits, "缺少儿童设计")
            require({t.get("source_person_id") for t in traits} == {"P1", "P2"}, "每名孩子须有双方来源线索")
            for t in traits:
                require(all(present(t.get(k)) for k in ("module", "observed_cue", "child_translation")), "特征设计不完整")
    for key in ("sibling_shared_trait", "sibling_difference", "scene", "composition", "test_objective"):
        require(present(spec.get(key)), f"缺少 {key}")
    return spec, resolved


def visual_prompt_for(spec, refs, optional_childhood=None):
    positions = {alias: i for i, r in enumerate(refs, 1) for alias in r.get("aliases", [r["id"]])}
    children_only = spec.get("output_subjects", "family") == "children_only"
    subject_line = ("创作一张恰好两个想象孩子的近景相貌试片；成人只作来源参考，不出现在画面中。外貌创作，不是遗传预测。"
                    if children_only else "创作一张自然写实的四人家庭合照：参考中的两位成人与两个想象孩子。外貌创作，不是遗传预测。")
    lines = ["Use case: photorealistic-natural",
             subject_line,
             "原始图片是面部形状与比例的主要依据；文字只指明重点。只使用本次显式输入，不借用会话中的其他人物或生成候选。"]
    for i, r in enumerate(refs, 1):
        purpose = "成人身份参考，人物归属见下列定位" if r["role"] == "adult_identity" else "同组人物的儿童阶段比例参考"
        lines.append(f"图 {i}：{purpose}。")
    for item in spec.get("reference_usage", []):
        subjects = "/".join(item["person_ids"]) or "同组儿童比例，不做逐人配对"
        lines.append(f"图 {positions[item['reference_id']]} 的{item['selector']}用于 {subjects}：{item['purpose']}")
        if item["limitations"]:
            lines.append("该图限制：" + "；".join(item["limitations"]))
    if optional_childhood:
        lines.append(f"如本次提供图 {len(refs) + 1}，它仅为同组人物的童年比例参考；若未提供，不假设看到了任何童年照片。")
    for p in spec["people"]:
        duty = "作为来源，核对可见关系" if children_only else "保留本人，重点核对"
        lines.append(f"{p['id']} = 输入图 {positions[p['reference_id']]} 的{p['selector']}。{duty}：{p['identity_lock']}")
        if p["uncertain_features"]:
            lines.append("观察限制：" + "；".join(p["uncertain_features"]))
    lines.append("孩子的脸应来自双方整体面部关系的自然重组，保留个体差异；不按百分比平均，不把眼鼻嘴机械拼接。")
    for c in spec["children"]:
        lines.append(f"{c['id']}：约 {c['age']} 岁{c['presentation']}。整体方向：{c['visual_brief']}")
        for link in c.get("facial_links", []):
            lines.append(f"面部重点 {link['region']}：参考 {link['source_person_id']}（图 {positions[link['reference_id']]}）的{link['observed_cue']}；年龄处理：{link['age_adaptation']}")
        if c.get("styling_note"):
            lines.append(f"儿童造型：{c['styling_note']}")
        if c.get("expression_note"):
            lines.append(f"目光与表情：{c['expression_note']}")
    lines += [f"共同点：{spec['sibling_shared_trait']}", f"区别：{spec['sibling_difference']}",
              "如有童年图，用其完整的儿童面部关系帮助年龄适配，而非复制某个孩子的整张脸。" +
              spec.get("childhood_binding", "未确认跨年龄逐人对应时，只作同组儿童比例参考，不自行识别配对。"),
              "不要照搬参考的发型、服装、场景或动作来制造相似感；低头、吹气、抬眼、镜片与妆容效果不当作固定五官。",
              "按年龄调整儿童面颊、鼻部长度和突出程度及下颌成熟感，同时保留眼形、眉眼位置、唇线、嘴角方向、鼻梁走向和鼻尖等可见辨识关系；不统一变成圆脸小圆鼻。",
              "每个孩子的面部都应呼应双方；不能只靠卷发、发色或肤色建立联系。兄妹有共同点但不是同脸换发型，无成人妆或夸张大眼，不自动复制成人痣和配饰。",
              "按具体造型要求形成干净日常的儿童头发，保留合适的真实发质；不要把自然凌乱理解成精心设计的蓬松乱发，不增加油腻脏污。",
              "不自动美化为更大更圆的眼睛、更窄的鼻翼、更小的嘴或尖下巴；保留来源可见的宽窄、长短、厚薄关系。普通感来自结构和造型，不靠添加皮肤缺陷。",
              "目光有符合活动的明确落点、与光源一致的细小反光，眼周和嘴角协调，不强制全员同款笑容。保留眼形与间距，不用扩大眼球制造活力。",
              f"场景与造型：{spec['scene']}", f"构图：{spec['composition']}"]
    if spec.get("scene_mode", "face_comparison") == "candid_lifestyle":
        lines.append("目标是生活瞬间：按具体活动安排不同步的动作与视线，不要求所有人同时看镜头、同方向歪头或同步微笑。保留身份，手自然参与活动，不刻意挤脸；不以皮肤缺陷或脏乱制造真实感。")
    else:
        face_count = "两张孩子的脸清楚" if children_only else "四脸清楚"
        lines.append(f"目标是面孔比较用的自然合影：角度、光线和表情便于核对，{face_count}。手放桌面、膝上或肩背，不托腮、不挤脸、不遮下颌；不把合影宣称为抓拍。")
    lines.append("真实皮肤与头身比例，无额外人物、文字或拼贴。")
    return "\n".join(lines) + "\n"


def prompt_for(spec, refs):
    if spec.get("guidance_mode", "feature_recipe") == "visual_first":
        return visual_prompt_for(spec, refs)
    positions = {alias: i for i, r in enumerate(refs, 1) for alias in r.get("aliases", [r["id"]])}
    lines = ["Use case: photorealistic-natural",
             "创作一张写实家庭合照：原始参考中的两位成人与两个想象孩子，恰好四人。这是外貌创作，不是遗传预测。",
             "只使用本次显式提供的参考图片；不调用其他家庭、旧候选或会话里其他人的面孔。"]
    for i, r in enumerate(refs, 1):
        lines.append(f"输入图 {i}，参考 ID {r['id']}，职责 {r['role']}。")
    for p in spec["people"]:
        # Multiple people can be grounded in the same full original image.
        index = positions.get(p["reference_id"])
        require(index is not None, "图内定位引用了重复别名，请统一参考 ID")
        lines.append(f"{p['id']} = 输入图 {index} 的{p['selector']}。成人保持：{p['identity_lock']}")
        for uncertainty in p["uncertain_features"]:
            lines.append(f"{p['id']} 观察限制：{uncertainty}")
    lines.append("成人相貌优先保持，不能仅靠相同眼镜和发型替代脸部保持。不要美化换脸或改变成人呈现。")
    for c in spec["children"]:
        lines.append(f"{c['id']}：约 {c['age']} 岁{c['presentation']}，为新创作的独立儿童面孔。")
        for t in c["traits"]:
            lines.append(f"{t['module']}参考 {t['source_person_id']} 的 {t['observed_cue']}；儿童化处理：{t['child_translation']}")
    lines += [f"兄妹共同点：{spec['sibling_shared_trait']}", f"兄妹结构区别：{spec['sibling_difference']}",
              "不做同脸换发型；儿童无成人妆容、不夸张大眼、不直接缩小成人鼻梁和下颌，不自动复制眼镜与痣。",
              "童年参考如有，仅用于对应人物的年龄比例；低头、吹气、抬眼等表情不当作固定解剖。",
              f"场景：{spec['scene']}", f"构图：{spec['composition']}",
              "这是相貌评测图：手放在桌面、膝上或肩背，不托腮、不挤压面颊、不遮挡下颌，保留可比较的自然脸部轮廓。",
              "自然皮肤与普通衣着、真实头身比例；四脸清楚，姿态不机械，手臂与手指合理；无多余人物、无文字水印、无拼贴。"]
    return "\n".join(lines) + "\n"


def write_run(registry, spec_path, output, spec, refs, prompt):
    request = {"prompt": prompt, "referenced_image_paths": [r["path"] for r in refs], "transparent_background": False}
    output = Path(output)
    require(not output.exists(), "输出目录已存在，请另开实验")
    output.mkdir(parents=True, exist_ok=False)
    (output / "prompt.txt").write_text(prompt, encoding="utf-8")
    dump(output / "imagegen-request.json", request)
    run = {"schema_version": 1, "status": "prepared_not_generated", "family_id": spec["family_id"],
           "spec_snapshot": spec, "references": refs, "user_acceptance": None,
           "registry_sha256": sha(registry), "spec_sha256": sha(spec_path),
           "request_sha256": sha(output / "imagegen-request.json"), "prompt_sha256": sha(output / "prompt.txt")}
    dump(output / "run.json", run)
    return run


def prepare(registry, spec_path, output):
    spec, refs = validate(registry, spec_path)
    return write_run(registry, spec_path, output, spec, refs, prompt_for(spec, refs))


def prepare_ablation(registry, spec_path, output, childhood_id):
    base, adult_refs = validate(registry, spec_path)
    require(base.get("guidance_mode") == "visual_first", "童年参考对照需要视觉优先方案")
    require(all(r["role"] == "adult_identity" for r in adult_refs), "对照基准只能包含成人原始参考")
    augmented = copy.deepcopy(base)
    augmented["source_reference_ids"].append(childhood_id)
    augmented, all_refs = validate(registry, spec_path, augmented)
    require(len(all_refs) == len(adult_refs) + 1 and all_refs[-1]["role"] == "childhood_proportions",
            "对照只允许增加一张不同的同组童年原图")
    require(all_refs[:-1] == adult_refs, "成人参考及顺序必须一致")
    # Compose once from adult-only observations. No child-photo-derived descriptions
    # may be copied into the base spec; that semantic check belongs to the agent.
    shared_prompt = visual_prompt_for(base, adult_refs, optional_childhood=all_refs[-1])
    output = Path(output)
    require(not output.exists(), "输出目录已存在，请另开实验")
    output.mkdir(parents=True, exist_ok=False)
    for key, study, refs in (("adult_only", base, adult_refs), ("with_childhood", augmented, all_refs)):
        write_run(registry, spec_path, output / key, study, refs, shared_prompt)
    pair = {"schema_version": 1, "family_id": base["family_id"], "status": "prepared_not_generated",
            "conditions": {key: f"{key}/run.json" for key in ("adult_only", "with_childhood")},
            "changed_input": childhood_id, "request_change": "one_additional_reference_image_only",
            "prompt_sha256": sha(output / "adult_only/prompt.txt"),
            "design_check": "Base observations must come from adult references only; requires human or agent review.",
            "samples_per_condition": 1, "pixel_seed_controlled": False,
            "user_acceptance": None,
            "interpretation": "Two exploratory samples; model randomness and output deviations remain uncontrolled."}
    dump(output / "pair.json", pair)
    verify_pair(output / "pair.json")
    return pair


def verify_run(path):
    path = Path(path)
    run = read(path)
    for r in run["references"]:
        require(Path(r["path"]).is_file() and sha(r["path"]) == r["sha256"], "准备后参考图发生变化")
    for name, key in (("imagegen-request.json", "request_sha256"), ("prompt.txt", "prompt_sha256")):
        require(sha(path.parent / name) == run[key], "准备后请求内容发生变化")
    request = read(path.parent / "imagegen-request.json")
    require(request["referenced_image_paths"] == [r["path"] for r in run["references"]], "参考载荷不一致")
    require(request["prompt"] == (path.parent / "prompt.txt").read_text(encoding="utf-8"), "提示词载荷不一致")
    return request


def verify_pair(path):
    path = Path(path)
    pair = read(path)
    baseline_path = path.parent / pair["conditions"]["adult_only"]
    augmented_path = path.parent / pair["conditions"]["with_childhood"]
    a, b = verify_run(baseline_path), verify_run(augmented_path)
    require({k: v for k, v in a.items() if k != "referenced_image_paths"} ==
            {k: v for k, v in b.items() if k != "referenced_image_paths"}, "对照的提示词或其他工具参数发生变化")
    require(b["referenced_image_paths"][:-1] == a["referenced_image_paths"] and
            len(b["referenced_image_paths"]) == len(a["referenced_image_paths"]) + 1, "对照不是只增加一张图")
    ra, rb = read(baseline_path), read(augmented_path)
    require(ra["family_id"] == rb["family_id"] == pair["family_id"], "对照家组不一致")
    require(all(r["role"] == "adult_identity" for r in ra["references"]), "对照基准不全是成人参考")
    extra = rb["references"][-1]
    require(extra["role"] == "childhood_proportions" and extra["family_id"] == pair["family_id"] and
            pair["changed_input"] in extra.get("aliases", [extra["id"]]), "对照额外输入不是指定家组的童年图")
    require(sha(baseline_path.parent / "prompt.txt") == pair["prompt_sha256"], "对照共享提示词发生变化")
    return a, b


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry")
    parser.add_argument("--spec")
    parser.add_argument("--output")
    parser.add_argument("--verify-run")
    parser.add_argument("--ablate-childhood", metavar="REFERENCE_ID")
    parser.add_argument("--verify-pair")
    args = parser.parse_args()
    try:
        if args.verify_pair:
            verify_pair(args.verify_pair)
            print("两条件提示词及其他参数完全一致，仅相差指定的同组童年图。")
        elif args.verify_run:
            verify_run(args.verify_run)
            print("参考组、图片哈希和生图载荷校验通过。")
        else:
            require(all((args.registry, args.spec, args.output)), "需提供 registry、spec 和 output")
            if args.ablate_childhood:
                pair = prepare_ablation(args.registry, args.spec, args.output, args.ablate_childhood)
                print(f"已准备家庭 {pair['family_id']} 的两条件单样本对照；尚未生图。")
            else:
                run = prepare(args.registry, args.spec, args.output)
                print(f"已准备家庭 {run['family_id']} 的一张测试，原始参考 {len(run['references'])} 张；尚未生图。")
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(2, f"错误: {exc}\n")


if __name__ == "__main__":
    main()
