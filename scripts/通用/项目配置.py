"""读取可分享的配置契约；本机配置不进入分享包。仅使用标准库。"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KEYS = {"schema_version", "project_name", "scene", "max_batch", "blender",
        "output_dir", "protect", "material_details", "plugin_environment"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"配置键重复：{key}")
        result[key] = value
    return result


def resolve_path(value, root=ROOT):
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def load_config(path, root=ROOT):
    """相对路径始终基于工具包根目录，不依赖终端当前目录。"""
    path = resolve_path(path, root)
    data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object)
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")
    if set(data) != KEYS:
        raise ValueError(f"配置字段不符；缺少：{sorted(KEYS - set(data))}；未知：{sorted(set(data) - KEYS)}")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("仅支持 schema_version=1")
    for key in ("project_name", "scene", "max_batch", "blender", "output_dir"):
        if not isinstance(data[key], str) or data[key] != data[key].strip():
            raise ValueError(f"{key} 必须是无首尾空格的字符串")
    if not data["project_name"] or any(c in data["project_name"] for c in "\r\n\0"):
        raise ValueError("project_name 不能为空或包含控制字符")
    if not data["output_dir"]:
        raise ValueError("output_dir 不能为空")
    if type(data["material_details"]) is not bool:
        raise ValueError("material_details 必须是布尔值")
    if not isinstance(data["protect"], list) or any(
            not isinstance(item, str) or not item.strip() or item != item.strip() for item in data["protect"]):
        raise ValueError("protect 必须是非空路径字符串的数组")
    env = data["plugin_environment"]
    if not isinstance(env, dict):
        raise ValueError("plugin_environment 必须是对象")
    for key, value in env.items():
        if key != "ADSK_APPLICATION_PLUGINS" and not re.fullmatch(r"VRAY_FOR_3DSMAX\d+_(MAIN|PLUGINS)", key):
            raise ValueError(f"不支持的插件环境变量：{key}")
        if not isinstance(value, str) or not value.strip() or any(c in value for c in "\r\n\0"):
            raise ValueError(f"插件环境变量 {key} 必须是非空目录字符串")
    return data


def inspect_config(data, root=ROOT):
    """只读检查；不创建目录、不启动软件、不验证软件授权。"""
    errors, warnings = [], []
    paths = {key: resolve_path(data[key], root) if data[key] else None
             for key in ("scene", "max_batch", "blender", "output_dir")}
    if sys.version_info < (3, 10):
        errors.append("工具入口需要 Python 3.10 或更新版本")
    if os.name != "nt":
        errors.append("本期 Max 安全审计仅支持 Windows")
    scene, executable, output = paths["scene"], paths["max_batch"], paths["output_dir"]
    if scene is None or not scene.is_file() or scene.suffix.lower() != ".max":
        errors.append("scene 必须指向存在的 .max 文件；不要使用配置样例的空值")
    if executable is None or not executable.is_file() or executable.name.lower() != "3dsmaxbatch.exe":
        errors.append("max_batch 必须指向实际安装的 3dsmaxbatch.exe")
    if paths["blender"] is None:
        warnings.append("未配置 Blender；不阻止 Max 审计，后续转换前需配置并验证")
    elif not paths["blender"].is_file() or paths["blender"].name.lower() != "blender.exe":
        errors.append("blender 必须为空或指向实际安装的 blender.exe")
    allowed = root.resolve() / "output" / "过程资产"
    # 同时检查逻辑目录和解析后的目录，拒绝通过目录联接逃逸工具包。
    if allowed.resolve() != allowed or output == allowed or not output.is_relative_to(allowed):
        errors.append("output_dir 必须位于工具包 output/过程资产 下的独立新批次目录")
    if output.exists():
        errors.append("输出批次已存在；请使用新批次，禁止覆盖或盲目重试")
    for parent in output.parents:
        if parent.exists() and not parent.is_dir():
            errors.append("输出目录的父路径存在同名文件")
            break
    protected = ([scene.parent] if scene is not None else []) + [resolve_path(p, root) for p in data["protect"]]
    for path in protected:
        if not path.is_dir():
            errors.append(f"保护目录不存在：{path}")
        if output == path or output.is_relative_to(path) or path.is_relative_to(output):
            errors.append(f"输出与保护目录重叠：{path}")
    environment = {}
    for key, raw in data["plugin_environment"].items():
        items = raw.split(";") if key == "ADSK_APPLICATION_PLUGINS" else [raw]
        directories = []
        for item in items:
            if not item.strip():
                errors.append(f"插件变量 {key} 含空目录")
                continue
            path = resolve_path(item.strip(), root)
            directories.append(str(path))
            if not path.is_dir():
                errors.append(f"插件目录不存在：{path}")
        environment[key] = ";".join(directories)
    warnings.append("文件存在不代表版本、插件加载、渲染授权或场景审计已通过")
    return {"status": "blocked" if errors else "ready_for_audit", "errors": errors,
            "warnings": warnings, "paths": {k: str(v) if v else None for k, v in paths.items()},
            "protect": [str(p) for p in protected], "plugin_environment": environment}
