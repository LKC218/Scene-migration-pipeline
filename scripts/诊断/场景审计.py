"""在 3ds Max Batch 中只读审计当前已加载的场景。"""

from __future__ import annotations

import json
import os
import platform
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pymxs import runtime as rt


OUTPUT_ENV = "BL_CONVERT_AUDIT_OUTPUT"


def text(value) -> str:
    try:
        return str(value)
    except Exception:
        return "<无法读取>"


def class_name(value) -> str:
    if value is None:
        return "None"
    try:
        return text(rt.classOf(value))
    except Exception:
        return type(value).__name__


def node_name(node) -> str:
    try:
        return text(node.name)
    except Exception:
        return "<未命名>"


def read_property(value, property_name: str):
    try:
        if rt.isProperty(value, property_name):
            return getattr(value, property_name)
    except Exception:
        pass
    return None


def collect_materials():
    materials = {}
    visited = set()

    def visit(material, owner: str):
        if material is None:
            return
        handle = int(rt.getHandleByAnim(material))
        key = str(handle)
        entry = materials.setdefault(
            key,
            {
                "handle": handle,
                "name": text(read_property(material, "name")),
                "class": class_name(material),
                "owners": [],
                "sub_material_count": 0,
                "sub_texture_count": 0,
            },
        )
        if owner not in entry["owners"] and len(entry["owners"]) < 30:
            entry["owners"].append(owner)
        if handle in visited:
            return
        visited.add(handle)

        try:
            entry["sub_material_count"] = int(rt.getNumSubMtls(material))
            for index in range(1, entry["sub_material_count"] + 1):
                visit(rt.getSubMtl(material, index), f"{entry['name']}[子材质{index}]")
        except Exception:
            pass

        try:
            entry["sub_texture_count"] = int(rt.getNumSubTexmaps(material))
        except Exception:
            pass

    for node in list(rt.objects):
        try:
            visit(node.material, node_name(node))
        except Exception:
            continue

    try:
        for index, material in enumerate(list(rt.sceneMaterials), start=1):
            visit(material, f"场景材质槽{index}")
    except Exception:
        pass

    return sorted(materials.values(), key=lambda item: (item["class"], item["name"]))


def collect_texture_graph():
    """递归检查材质子贴图，避免只统计 Bitmap 而漏掉缺失程序贴图。"""
    rows = {}
    visited = set()
    errors = []

    def visit(value, is_material=False):
        if value is None:
            return
        handle = int(rt.getHandleByAnim(value))
        if handle in visited:
            return
        visited.add(handle)
        try:
            for index in range(1, int(rt.getNumSubTexmaps(value)) + 1):
                texture = rt.getSubTexmap(value, index)
                if texture is None:
                    continue
                texture_handle = int(rt.getHandleByAnim(texture))
                rows[str(texture_handle)] = {
                    "handle": texture_handle,
                    "name": text(read_property(texture, "name")),
                    "class": class_name(texture),
                }
                visit(texture)
            if is_material:
                for index in range(1, int(rt.getNumSubMtls(value)) + 1):
                    visit(rt.getSubMtl(value, index), is_material=True)
        except Exception as exc:
            errors.append({"handle": handle, "error": text(exc)})

    for material in list(rt.sceneMaterials):
        visit(material, is_material=True)
    for node in list(rt.objects):
        material = read_property(node, "material")
        if material is not None:
            visit(material, is_material=True)
    return {"textures": list(rows.values()), "errors": errors}


def collect_bitmap_textures():
    rows = []
    scene_directory = Path(text(rt.maxFilePath))
    try:
        instances = list(rt.getClassInstances(rt.Bitmaptexture))
    except Exception:
        instances = []

    for texture in instances:
        filename = read_property(texture, "filename")
        filename_text = text(filename) if filename else ""
        resolved_path = Path(filename_text) if filename_text else None
        if resolved_path is not None and not resolved_path.is_absolute():
            resolved_path = scene_directory / resolved_path
        rows.append(
            {
                "name": text(read_property(texture, "name")),
                "class": class_name(texture),
                "filename": filename_text,
                "resolved_path": text(resolved_path) if resolved_path else "",
                "exists": bool(resolved_path and resolved_path.is_file()),
            }
        )
    return rows


def collect_assets():
    rows = []
    try:
        count = int(rt.AssetManager.GetNumAssets())
    except Exception:
        return rows

    for index in range(1, count + 1):
        try:
            asset = rt.AssetManager.GetAssetByIndex(index)
        except Exception:
            continue

        def call(method_name: str) -> str:
            try:
                return text(getattr(asset, method_name)())
            except Exception:
                return ""

        full_path = call("GetFullFilePath")
        rows.append(
            {
                "index": index,
                "type": call("GetType"),
                "filename": call("GetFileName"),
                "full_path": full_path,
                "exists": bool(full_path and Path(full_path).is_file()),
            }
        )
    return rows


def collect_nodes():
    class_counter = Counter()
    superclass_counter = Counter()
    missing_class_nodes = []
    hidden_count = 0
    frozen_count = 0

    nodes = list(rt.objects)
    for node in nodes:
        current_class = class_name(node)
        class_counter[current_class] += 1
        try:
            superclass_counter[text(rt.superClassOf(node))] += 1
        except Exception:
            superclass_counter["<无法读取>"] += 1
        if "missing" in current_class.lower():
            missing_class_nodes.append(node_name(node))
        try:
            hidden_count += int(bool(node.isHidden))
        except Exception:
            pass
        try:
            frozen_count += int(bool(node.isFrozen))
        except Exception:
            pass

    return {
        "total": len(nodes),
        "hidden": hidden_count,
        "frozen": frozen_count,
        "by_class": dict(class_counter.most_common()),
        "by_superclass": dict(superclass_counter.most_common()),
        "missing_class_nodes": missing_class_nodes,
    }


def main() -> int:
    output_value = os.environ.get(OUTPUT_ENV, "").strip()
    if not output_value:
        raise RuntimeError(f"缺少环境变量 {OUTPUT_ENV}")

    output_path = Path(output_value)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "audit": {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "read_only_scene_audit",
            "python": platform.python_version(),
        },
        "scene": {
            "path": text(rt.maxFilePath),
            "name": text(rt.maxFileName),
            "max_version": [text(value) for value in rt.maxVersion()],
            "renderer_class": class_name(rt.renderers.current),
            "renderer_name": text(rt.renderers.current),
        },
    }

    try:
        report["nodes"] = collect_nodes()
        report["materials"] = collect_materials()
        report["texture_graph"] = collect_texture_graph()
        report["bitmap_textures"] = collect_bitmap_textures()
        report["assets"] = collect_assets()

        material_classes = Counter(item["class"] for item in report["materials"])
        texture_classes = Counter(item["class"] for item in report["bitmap_textures"])
        report["summary"] = {
            "material_count": len(report["materials"]),
            "material_classes": dict(material_classes.most_common()),
            "bitmap_texture_count": len(report["bitmap_textures"]),
            "bitmap_texture_classes": dict(texture_classes.most_common()),
            "missing_bitmap_count": sum(
                1 for item in report["bitmap_textures"] if not item["exists"]
            ),
            "asset_count": len(report["assets"]),
            "missing_asset_count": sum(1 for item in report["assets"] if not item["exists"]),
        }
        report["status"] = "ok"
    except Exception as exc:
        report["status"] = "error"
        report["error"] = text(exc)
        report["traceback"] = traceback.format_exc()

    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"AUDIT_REPORT={output_path}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    main()
