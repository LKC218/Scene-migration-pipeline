"""运行独立安全审计，记录输入身份、资源候选及保护范围哈希；不执行转换。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def snapshot(root):
    return {
        str(path.relative_to(root)): {"bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def assess(report, expected_scene, source_root):
    """只判断是否存在阻塞；无阻塞也不代表材质已可无损转换。"""
    blockers = []
    scene = report.get("scene", {})
    actual_scene = Path(scene.get("path", "")) / scene.get("name", "")
    if actual_scene.resolve() != expected_scene.resolve():
        blockers.append("审计报告的源场景身份不匹配")
    if report.get("status") != "ok":
        blockers.append("场景审计未成功完成")
    missing_materials = [m for m in report.get("materials", []) if "missing" in m["class"].lower()]
    missing_maps = [m for m in report.get("texture_graph", {}).get("textures", []) if "missing" in m["class"].lower()]
    if missing_materials:
        blockers.append(f"存在 {len(missing_materials)} 个缺失插件材质，禁止近似替代后宣称完整迁移")
    if missing_maps:
        blockers.append(f"存在 {len(missing_maps)} 个缺失插件贴图")
    if "missing" in scene.get("renderer_class", "").lower():
        blockers.append("原渲染器缺失，无法生成可信原场景对照渲染")
    if report.get("nodes", {}).get("missing_class_nodes"):
        blockers.append("存在缺失插件场景对象，需确认几何或灯光影响")
    if report.get("texture_graph", {}).get("errors"):
        blockers.append("部分材质子图读取失败，需进一步检查")
    if report.get("material_detail_status") in ("partial", "error"):
        blockers.append("原始材质参数提取不完整，需检查详细读取异常")
    if report.get("summary", {}).get("missing_asset_count", 0):
        blockers.append("Asset Manager 存在未解析外部资源，需核查")

    by_name = {}
    for path in source_root.rglob("*"):
        if path.is_file():
            by_name.setdefault(path.name.casefold(), []).append(str(path))
    resources = []
    for item in report.get("bitmap_textures", []):
        raw = item.get("filename", "")
        candidates = by_name.get(Path(raw).name.casefold(), []) if raw else []
        resources.append({**item, "local_candidates": candidates,
                          "resolution": "已解析" if item.get("exists") else "唯一候选待重链接" if len(candidates) == 1 else "歧义" if candidates else "缺失"})
    if any(r["resolution"] in ("歧义", "缺失") for r in resources):
        blockers.append("位图引用存在缺失或同名歧义")
    return {"status": "blocked" if blockers else "needs_material_mapping_review",
            "blockers": blockers, "missing_materials": missing_materials,
            "missing_maps": missing_maps, "resources": resources,
            "conversion_performed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--max-batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protect", type=Path, action="append", default=[])
    parser.add_argument("--material-details", action="store_true", help="附加原始材质参数、插槽和对象引用链，不导出模型")
    args = parser.parse_args()
    scene = args.scene.resolve()
    executable = args.max_batch.resolve()
    output = args.output.resolve()
    if not scene.is_file() or scene.suffix.lower() != ".max" or not executable.is_file():
        parser.error("源 .max 或 Max Batch 程序不存在")
    protected = [scene.parent, *(p.resolve() for p in args.protect)]
    for path in protected:
        if not path.is_dir() or output.is_relative_to(path):
            parser.error("保护路径必须存在，输出不得位于保护路径内部")
    output.mkdir(parents=True, exist_ok=False)
    before = {str(path): snapshot(path) for path in protected}
    write_json(output / "保护范围快照.json", before)
    report_path = output / "场景审计.json"
    script_name = "材质依赖审计.py" if args.material_details else "场景审计.py"
    command = [str(executable), str(Path(__file__).with_name(script_name)),
               "-sceneFile", str(scene), "-safescene", "on",
               "-listenerlog", str(output / "3dsmax侦听器日志.log"),
               "-log", str(output / "3dsmax系统日志.log")]
    env = os.environ.copy()
    env["BL_CONVERT_AUDIT_OUTPUT"] = str(report_path)
    started = time.time()
    execution = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
                 "scene": str(scene), "source_sha256": sha256(scene), "command": command,
                 "safe_scene": True, "status": "running"}
    write_json(output / "执行记录.json", execution)
    print(f"开始安全审计：{scene.name}；输出：{output}", flush=True)
    with (output / "批处理输出.log").open("wb") as log:
        result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    execution.update(returncode=result.returncode, elapsed_seconds=round(time.time() - started, 3),
                     status="finished", source_sha256_after=sha256(scene))
    after = {str(path): snapshot(path) for path in protected}
    unchanged = before == after
    write_json(output / "保护范围校验.json", {"unchanged": unchanged, "after": after})
    execution["protected_files_unchanged"] = unchanged
    write_json(output / "执行记录.json", execution)
    if report_path.is_file():
        gate = assess(json.loads(report_path.read_text(encoding="utf-8")), scene, scene.parent)
    else:
        gate = {"status": "blocked", "blockers": ["没有生成场景审计报告"], "conversion_performed": False}
    if not unchanged:
        gate["status"] = "blocked"
        gate["blockers"].append("保护范围文件发生变化，需核查")
    if result.returncode:
        gate["status"] = "blocked"
        gate["blockers"].append(f"Max Batch 非零退出码 {result.returncode}，必须结合日志核查")
    write_json(output / "迁移前置检查.json", gate)
    print(json.dumps({"status": gate["status"], "blockers": gate["blockers"],
                      "protected_files_unchanged": unchanged}, ensure_ascii=False), flush=True)
    return 2 if gate["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
