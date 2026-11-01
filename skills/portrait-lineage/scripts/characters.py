#!/usr/bin/env python3
"""List/resolve characters and record user-requested names; never approves appearance."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unicodedata


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def key(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


def catalog(directory):
    directory = Path(directory)
    if not directory.exists():
        return []
    rows, ids = [], set()
    for path in sorted(directory.glob("*.json")):
        profile = json.loads(path.read_text(encoding="utf-8"))
        require(isinstance(profile, dict), f"档案不是对象: {path.name}")
        require(text(profile.get("id")) and text(profile.get("display_name")), f"缺少 ID 或名字: {path.name}")
        require(profile["id"] not in ids, f"重复人物 ID: {profile['id']}")
        aliases = profile.get("aliases", [])
        require(isinstance(aliases, list) and all(text(a) for a in aliases), f"别名格式错误: {path.name}")
        ids.add(profile["id"])
        rows.append({"profile_path": str(path.resolve()), "profile": profile})
    return rows


def summary(row):
    p = row["profile"]
    return {"id": p["id"], "display_name": p["display_name"], "aliases": p.get("aliases", []),
            "status": p.get("status", "draft"), "version": p.get("version"),
            "profile_path": row["profile_path"], "age_years": p.get("age_years", p.get("age"))}


def user_review(value):
    return isinstance(value, dict) and value.get("confirmed_by") == "user" and text(value.get("note"))


def find(directory, name):
    require(text(name), "需要角色名字、别名或 ID")
    matches = [r for r in catalog(directory)
               if key(name) in {key(n) for n in [r["profile"]["id"], r["profile"]["display_name"],
                                               *r["profile"].get("aliases", [])]}]
    require(matches, "未找到角色；请查看角色列表，不自动创建同名人物")
    require(len(matches) == 1, "名字不唯一，请按 ID 选择: " + ", ".join(r["profile"]["id"] for r in matches))
    return matches[0]


def resolve(directory, name):
    row = find(directory, name)
    p = row["profile"]
    require(p.get("schema_version") == 1, "不支持的档案版本")
    require(type(p.get("version")) is int and p["version"] > 0, "缺少有效角色版本")
    require(p.get("status") == "approved" and user_review(p.get("review")), "角色外貌尚未确认")
    naming = p.get("name_review", {})
    require(user_review(naming) and naming.get("status") == "confirmed", "角色名字尚未确认，或旧档案缺少命名记录")
    require(p.get("kind") in {"real_person", "fictional_character"}, "未知角色类型")
    if p["kind"] == "real_person":
        permission = p.get("permission", {})
        require(permission.get("reference_use") is True and text(permission.get("note")), "缺少参考使用说明")
    refs = p.get("references", [])
    require(isinstance(refs, list) and refs, "缺少角色参考")
    by_id = {}
    for ref in refs:
        require(isinstance(ref, dict) and text(ref.get("id")) and ref["id"] not in by_id, "参考 ID 缺失或重复")
        by_id[ref["id"]] = ref
    require(p.get("master_reference_id") in by_id, "缺少主图绑定")
    master = by_id[p["master_reference_id"]]
    primary_role = "current_photo" if p["kind"] == "real_person" else "approved_reference"
    require(master.get("role") == primary_role, "主图必须是真人原图或已认可的虚构角色主图")
    views = p.get("identity_views", {})
    require(user_review(views), "多视图尚未确认或未记录用户跳过")
    require(views.get("status") in {"approved", "approved_partial", "skipped_by_user"}, "多视图仍待确认")
    approved = views.get("approved_reference_ids", [])
    require(isinstance(approved, list) and all(text(r) for r in approved), "辅助参考列表格式错误")
    require(len(approved) == len(set(approved)), "辅助参考重复")
    require((views["status"] == "skipped_by_user" and not approved)
            or (views["status"] != "skipped_by_user" and bool(approved)), "视图状态与可用参考不一致")
    for rid in approved:
        require(rid in by_id and by_id[rid].get("role") == "inferred_view", "辅助图须保留 inferred_view 身份")

    def materialize(ref):
        require(text(ref.get("path")), "参考缺少路径")
        path = (Path(row["profile_path"]).parent / ref["path"]).resolve()
        require(path.is_file() and path.stat().st_size > 0, f"参考文件缺失或为空: {ref['id']}")
        return dict(ref, path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    return dict(summary(row), master_reference=materialize(master),
                optional_inferred_references=[materialize(by_id[rid]) for rid in approved],
                identity_views_status=views["status"],
                note="仅解析用户确认记录；辅助图按任务需要选用。未验证实际相貌或授权真实性。")


def rename(directory, name, new_name, confirmation, keep_old_name=False):
    """Record an explicit name decision, preserving identity, images and prior version."""
    require(text(new_name), "新名字不能为空")
    require(text(confirmation), "需要用户明确取名或改名的原话；不能由助手代签")
    directory = Path(directory)
    require(directory.is_dir(), "角色目录不存在")
    # Local cooperative lock prevents two CLI renames from losing one another's edits.
    lock = directory / ".rename.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError("有改名操作未结束；检查后再重试") from exc
    temp_path = None
    try:
        row = find(directory, name)
        p = row["profile"]
        require(p.get("schema_version") == 1, "不支持的档案版本")
        require(type(p.get("version")) is int and p["version"] > 0, "缺少有效角色版本")
        new_name = new_name.strip()
        already_confirmed = p.get("name_review", {}).get("status") == "confirmed" and user_review(p.get("name_review"))
        require(new_name != p["display_name"] or not already_confirmed, "名字已经确认且没有变化")
        aliases = [a for a in p.get("aliases", []) if key(a) != key(new_name)]
        if keep_old_name and key(p["display_name"]) != key(new_name):
            aliases.append(p["display_name"])
        aliases = list({key(a): a for a in aliases}.values())
        claimed = {key(n) for n in [p["id"], new_name, *aliases]}
        for other in catalog(directory):
            q = other["profile"]
            if q["id"] != p["id"]:
                require(not claimed.intersection(key(n) for n in [q["id"], q["display_name"], *q.get("aliases", [])]),
                        "新名字或保留别名与其他角色冲突: " + q["id"])
        path = Path(row["profile_path"])
        original = path.read_bytes()
        require(json.loads(original) == p, "档案在读取后发生变化，请重新检查")
        updated = copy.deepcopy(p)
        updated.update(display_name=new_name, aliases=aliases, version=p["version"] + 1)
        updated["name_review"] = dict(p.get("name_review", {}), status="confirmed", confirmed_by="user", note=confirmation)
        history = directory / "history" / path.stem / f"v{p['version']:04d}.json"
        history.parent.mkdir(parents=True, exist_ok=True)
        with history.open("xb") as file:
            file.write(original)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix=".rename-", delete=False) as file:
            temp_path = Path(file.name)
            json.dump(updated, file, ensure_ascii=False, indent=2)
            file.write("\n")
        require(path.read_bytes() == original, "档案被其他程序修改；旧版本已备份，未覆盖当前档案")
        os.replace(temp_path, path)
        return dict(summary({"profile_path": str(path), "profile": updated}), history_path=str(history.resolve()),
                    note="只记录名字确认；外貌、视图、主图和年龄状态不变。历史路径仍相对原档案目录解析。")
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("list", "resolve", "rename"):
        child = sub.add_parser(command)
        child.add_argument("--profiles", default="portrait-data/profiles")
        if command != "list":
            child.add_argument("--name", required=True)
        if command == "rename":
            child.add_argument("--to", required=True)
            child.add_argument("--confirmation", required=True, help="用户明确取名或改名的原话")
            child.add_argument("--keep-old-name", action="store_true", help="仅当用户要求保留旧名为别名时使用")
    args = parser.parse_args()
    try:
        if args.command == "list":
            result = [summary(r) for r in catalog(args.profiles)]
        elif args.command == "rename":
            result = rename(args.profiles, args.name, args.to, args.confirmation, args.keep_old_name)
        else:
            result = resolve(args.profiles, args.name)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
        parser.exit(2, f"错误: {exc}\n")


if __name__ == "__main__":
    main()
