"""在安全 Max Batch 中提取材质原始参数与引用图，不保存或修改源场景。"""

from __future__ import annotations

import json
import math
import os
import runpy
import traceback
from collections import Counter
from pathlib import Path
from pymxs import runtime as rt


BASE = runpy.run_path(str(Path(__file__).with_name("场景审计.py")))
class_name = BASE["class_name"]
read_property = BASE["read_property"]
graph = {}
references = {}
errors = []


def handle_of(value):
    return int(rt.getHandleByAnim(value))


def encode(value, depth=0):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"non_finite": str(value)}
    kind = class_name(value)
    superclass = str(rt.superClassOf(value))
    if superclass.lower() in ("material", "texturemap"):
        handle = handle_of(value)
        visit(value)
        return {"ref": handle, "class": kind}
    if depth > 5:
        return {"unexpanded": kind}
    if kind in ("Array", "ArrayParameter") or isinstance(value, (list, tuple)):
        return [encode(item, depth + 1) for item in value]
    if kind in ("Color", "AColor"):
        return {"type": kind, "value": [float(value.r), float(value.g), float(value.b), float(value.a)]}
    if kind in ("Point2", "Point3", "Point4", "Quat", "EulerAngles"):
        fields = ("x", "y") if kind == "Point2" else ("x", "y", "z", "w") if kind in ("Point4", "Quat") else ("x", "y", "z")
        return {"type": kind, "value": [float(getattr(value, field)) for field in fields]}
    if kind == "Matrix3":
        return {"type": kind, "rows": [encode(getattr(value, f"row{i}"), depth + 1) for i in range(1, 5)]}
    if kind.lower() in ("standarduvgen", "standardtextureoutput") or superclass.lower() in ("uvgen", "textureoutput"):
        return {"type": kind, "properties": properties(value, depth + 1)}
    if kind in ("Integer", "Integer64"):
        return int(value)
    if kind in ("Float", "Double"):
        number = float(value)
        return number if math.isfinite(number) else {"non_finite": str(number)}
    return {"type": kind, "text": str(value)[:1000], "not_lossless": True}


def properties(value, depth=0):
    result = {}
    for prop in rt.getPropNames(value):
        name = str(prop)
        try:
            result[name] = encode(rt.getProperty(value, prop), depth + 1)
        except Exception as exc:
            result[name] = {"read_error": str(exc)}
            errors.append({"class": class_name(value), "property": name, "error": str(exc)})
    return result


def visit(value):
    handle = handle_of(value)
    key = str(handle)
    if key in graph:
        return
    superclass = str(rt.superClassOf(value))
    row = {"handle": handle, "name": str(read_property(value, "name")), "class": class_name(value),
           "superclass": superclass, "properties": {}, "texture_slots": [], "material_slots": []}
    graph[key] = row
    references[key] = value
    row["properties"] = properties(value)
    for index in range(1, int(rt.getNumSubTexmaps(value)) + 1):
        texture = rt.getSubTexmap(value, index)
        slot = {"index": index, "name": str(rt.getSubTexmapSlotName(value, index)),
                "ref": handle_of(texture) if texture is not None else None}
        row["texture_slots"].append(slot)
        if texture is not None:
            visit(texture)
    if superclass.lower() == "material":
        for index in range(1, int(rt.getNumSubMtls(value)) + 1):
            material = rt.getSubMtl(value, index)
            row["material_slots"].append({"index": index, "ref": handle_of(material) if material is not None else None})
            if material is not None:
                visit(material)


def node_row(node):
    material = read_property(node, "material")
    if material is not None:
        visit(material)
    parent = read_property(node, "parent")
    return {"handle": handle_of(node), "name": str(node.name), "class": class_name(node),
            "superclass": str(rt.superClassOf(node)), "material_ref": handle_of(material) if material is not None else None,
            "parent_ref": handle_of(parent) if parent is not None else None,
            "transform": encode(node.transform), "hidden": bool(node.isHidden),
            "renderable": read_property(node, "renderable")}


def main():
    BASE["main"]()
    path = Path(os.environ["BL_CONVERT_AUDIT_OUTPUT"])
    report = json.loads(path.read_text(encoding="utf-8"))
    try:
        nodes = [node_row(node) for node in rt.objects]
        for material in rt.sceneMaterials:
            visit(material)
        # 材质编辑器等位置可能保留未使用的 Bitmap，单独纳入图后再判断是否可达。
        for texture in rt.getClassInstances(rt.Bitmaptexture):
            visit(texture)
        lights = [{"handle": handle_of(node), "name": str(node.name), "class": class_name(node),
                   "properties": properties(node), "transform": encode(node.transform)} for node in rt.lights]
        cameras = [{"handle": handle_of(node), "name": str(node.name), "class": class_name(node),
                    "properties": properties(node), "transform": encode(node.transform)} for node in rt.cameras]
        report["material_details"] = {"schema_version": 1, "graph": graph, "nodes": nodes,
            "lights": lights, "cameras": cameras, "read_errors": errors,
            "graph_classes": dict(Counter(row["class"] for row in graph.values())),
            "units": {"system_type": str(rt.units.SystemType), "system_scale": float(rt.units.SystemScale)},
            "renderer_properties": properties(rt.renderers.current),
            "note": "原值快照；不能直接视为 Blender 参数或已验证的等价映射。未展开值明确标记 not_lossless。"}
        report["material_detail_status"] = "ok" if not errors else "partial"
    except Exception:
        report["status"] = "error"
        report["material_detail_status"] = "error"
        report["detail_error"] = traceback.format_exc()
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"材质参数快照：{report.get('material_detail_status')}，图节点 {len(graph)}")


if __name__ == "__main__":
    main()
