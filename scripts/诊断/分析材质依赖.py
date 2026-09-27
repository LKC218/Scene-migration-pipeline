"""离线追踪材质快照中的缺失项，不启动 Max、不更改材质或丢弃未知分支。"""

import argparse
import json
from collections import defaultdict, deque
from pathlib import Path


def iter_refs(value, path=""):
    if isinstance(value, dict):
        if "ref" in value:
            yield str(value["ref"]), path
        else:
            for key, item in value.items():
                yield from iter_refs(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_refs(item, f"{path}[{index}]")


def matching_strings(value, expected, path=""):
    if isinstance(value, str) and value == expected:
        yield path
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from matching_strings(item, expected, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from matching_strings(item, expected, f"{path}[{index}]")


def edge_controls(row, property_path):
    props = {key.lower(): value for key, value in row.get("properties", {}).items()}
    name = property_path.lower()
    controls = {}
    for key in (name + "_on", name + "enabled"):
        if key in props and isinstance(props[key], bool):
            controls[key] = props[key]
    key = name + "_multiplier"
    if key in props and isinstance(props[key], (int, float)):
        controls[key] = props[key]
    disabled = any(value is False for value in controls.values()) or any(
        key.endswith("_multiplier") and value == 0 for key, value in controls.items())
    return controls, disabled


def build_reverse(graph):
    reverse = defaultdict(list)
    for source, row in graph.items():
        named_targets = set()
        for target, property_path in iter_refs(row.get("properties", {})):
            if target not in graph:
                continue
            named_targets.add(target)
            controls, disabled = edge_controls(row, property_path)
            reverse[target].append({"parent": source, "child": target,
                                    "property": property_path, "controls": controls, "disabled": disabled})
        # 只有未被属性快照覆盖的引用才补插槽边；未知开关保持待核实。
        for kind in ("texture_slots", "material_slots"):
            for slot in row.get(kind, []):
                target = str(slot.get("ref"))
                if target in graph and target not in named_targets:
                    reverse[target].append({"parent": source, "child": target,
                        "property": f"{kind}[{slot['index']}]", "controls": {}, "disabled": False})
    return reverse


def trace(target, reverse, owners):
    queue = deque([(str(target), False, [])])
    visited = set()
    routes = []
    while queue:
        current, disabled, chain = queue.popleft()
        state = (current, disabled)
        if state in visited:
            continue
        visited.add(state)
        for owner in owners.get(current, []):
            routes.append({"owner": owner, "disabled_by_explicit_control": disabled,
                           "chain_from_issue_to_owner": chain})
        for edge in reverse.get(current, []):
            queue.append((edge["parent"], disabled or edge["disabled"], chain + [edge]))
    active = {route["owner"]["handle"] for route in routes if not route["disabled_by_explicit_control"]}
    all_owners = {route["owner"]["handle"] for route in routes}
    return {"classification": "bound_enabled_or_unknown" if active else "bound_but_explicitly_disabled" if all_owners else "no_object_route_found",
            "bound_owner_count": len(all_owners), "enabled_or_unknown_owner_count": len(active), "routes": routes}


def analyze(report):
    details = report["material_details"]
    graph = details["graph"]
    reverse = build_reverse(graph)
    owners = defaultdict(list)
    for node in details["nodes"]:
        if node.get("material_ref") is not None:
            owners[str(node["material_ref"])].append({key: node[key] for key in ("handle", "name", "class", "hidden", "renderable")})
    for group in ("lights", "cameras"):
        for node in details[group]:
            for target, prop in iter_refs(node["properties"]):
                owners[target].append({"handle": node["handle"], "name": node["name"], "class": node["class"], "property": prop})
    for target, prop in iter_refs(details.get("renderer_properties", {})):
        owners[target].append({"handle": "renderer", "name": "渲染器", "property": prop})
    issues = []
    for handle, row in graph.items():
        reasons = []
        if "missing" in row["class"].lower():
            reasons.append("缺失插件")
        if row["class"] == "Bitmaptexture" and not row["properties"].get("filename"):
            reasons.append("位图文件名为空")
        if reasons:
            issues.append({"handle": handle, "name": row["name"], "class": row["class"], "reasons": reasons,
                           **trace(handle, reverse, owners)})
    asset_issues = []
    for asset in report.get("assets", []):
        if asset.get("exists"):
            continue
        matches = []
        for handle, row in graph.items():
            props = list(matching_strings(row["properties"], asset["filename"]))
            if props:
                matches.append({"handle": handle, "class": row["class"], "name": row["name"], "properties": props,
                                **trace(handle, reverse, owners)})
        asset_issues.append({"filename": asset["filename"], "type": asset["type"], "graph_matches": matches,
                             "note": "无对象路径不等于全场景无用途；尚需检查环境、修改器及编辑器等引用。"})
    affected = set()
    for issue in issues:
        affected.update(route["owner"]["handle"] for route in issue["routes"] if not route["disabled_by_explicit_control"])
    for issue in asset_issues:
        for match in issue["graph_matches"]:
            affected.update(route["owner"]["handle"] for route in match["routes"] if not route["disabled_by_explicit_control"])
    return {"schema_version": 1, "source_scene": report["scene"], "graph_count": len(graph),
            "issues": issues, "unresolved_assets": asset_issues,
            "enabled_or_unknown_owner_count": len(affected), "read_error_count": len(details["read_errors"]),
            "full_conversion_allowed": False,
            "limitations": ["仅追踪快照覆盖的材质、灯光、相机和渲染器引用；不完整覆盖环境及修改器。",
                            "多材质槽已绑定不代表对应面一定使用；面材质编号需后续几何验证。",
                            "明确关闭的开关或零贴图乘数用于标识禁用；未知复杂分支不会被推断为无影响。",
                            "这是依赖与参数快照分析，不是渲染效果或转换验收。"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    print(json.dumps({"issues": [{"handle": i["handle"], "class": i["class"], "classification": i["classification"],
                                 "owners": i["enabled_or_unknown_owner_count"]} for i in result["issues"]],
                      "affected_owners": result["enabled_or_unknown_owner_count"], "read_errors": result["read_error_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
