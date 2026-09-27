"""按显式白名单检查、打包和复核工具包；不包含原模型、案例或本机配置。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import stat
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from 项目配置 import ROOT
from 生成六步指南 import SOURCE as GUIDE_SOURCE, TEMPLATE as GUIDE_TEMPLATE, OUTPUT as GUIDE_OUTPUT, render as render_guide


MANIFEST = "configs/分享白名单.json"
INVENTORY = "分享清单.json"
FLOWCHART = "docs/使用指南/图示/场景迁移与优化-流程图.svg"
LIMIT = 2 * 1024 * 1024
PRIVATE = [
    ("本机绝对路径", re.compile(r"\b[A-Za-z]:[/\\]")),
    ("用户主目录", re.compile(r"/(?:Users|home)/[\w.-]+/")),
    ("私钥", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("疑似访问密钥", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
]


def safe_name(name):
    if not isinstance(name, str):
        raise ValueError("分享路径必须是字符串")
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError(f"非法分享路径：{name}")
    if path.as_posix() != name or any(part.startswith(".") for part in path.parts) and name not in (".gitignore", ".gitattributes"):
        raise ValueError(f"分享路径必须规范且不能包含隐藏目录：{name}")
    if path.suffix not in (".md", ".py", ".json") and name not in (
            ".gitignore", ".gitattributes", FLOWCHART, GUIDE_TEMPLATE, GUIDE_OUTPUT, "scripts/验证/测试六步页面交互.cjs"):
        raise ValueError(f"分享文件类型不在许可范围：{name}")
    if any(part in ("源工程", "output", "node_modules", "__pycache__", "历史案例", "历史归档", "案例记录", "参考图") for part in path.parts):
        raise ValueError(f"禁止分享资产、缓存或历史目录：{name}")
    if name.endswith(".local.json"):
        raise ValueError("禁止分享本机配置")
    return name


def read_manifest(root):
    data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("files"), list):
        raise ValueError("分享白名单格式错误")
    names = [safe_name(name) for name in data["files"]]
    if len(names) != len(set(name.casefold() for name in names)):
        raise ValueError("分享白名单存在重复路径")
    required = {"README.md", "AGENTS.md", MANIFEST, "configs/项目配置.example.json"}
    if not required.issubset(names):
        raise ValueError("分享白名单缺少必要入口")
    return names


def read_files(root):
    contents = {}
    for name in read_manifest(root):
        path = root / name
        for part in (path, *path.parents):
            if part == root:
                break
            attributes = getattr(part.lstat(), "st_file_attributes", 0)
            if part.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
                raise ValueError(f"分享路径禁止符号链接或目录联接：{name}")
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size > LIMIT:
            raise ValueError(f"分享文件越界、缺失或超过 2 MiB：{name}")
        contents[name] = path.read_bytes()
    return contents


def validate_flowchart(body):
    """只接受离线静态图示；不将SVG支持扩大为可执行网页内容。"""
    if re.search(r"<!DOCTYPE|<!ENTITY|<\?", body, re.IGNORECASE):
        raise ValueError("流程图不允许实体声明或处理指令")
    root = ET.fromstring(body)
    namespace = "{http://www.w3.org/2000/svg}"
    if root.tag != namespace + "svg":
        raise ValueError("流程图必须是标准SVG")
    tags = {"svg", "title", "desc", "defs", "marker", "g", "path", "rect", "line",
            "polyline", "polygon", "circle", "text", "tspan"}
    attributes = {"id", "role", "aria-labelledby", "width", "height", "viewBox", "x", "y",
                  "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "d", "points",
                  "fill", "stroke", "stroke-width", "stroke-linejoin", "stroke-linecap",
                  "font-family", "font-size", "font-weight", "text-anchor", "opacity",
                  "marker-end", "markerWidth", "markerHeight", "refX", "refY", "orient",
                  "markerUnits", "transform", "dominant-baseline", "preserveAspectRatio"}
    for element in root.iter():
        if element.tag not in {namespace + tag for tag in tags}:
            raise ValueError("流程图包含非静态或不支持的元素")
        for key, value in element.attrib.items():
            if key not in attributes:
                raise ValueError(f"流程图包含不支持的属性：{key}")
            if "url" in value.lower() and not re.fullmatch(r"url\(#[A-Za-z][\w.-]*\)", value):
                raise ValueError("流程图不允许外部资源")


def validate_contents(contents):
    errors = []
    guide_files = {GUIDE_SOURCE, GUIDE_TEMPLATE, GUIDE_OUTPUT}
    if not guide_files.issubset(contents):
        raise ValueError("分享包缺少六步指南的内容、模板或HTML")
    expected = render_guide(contents[GUIDE_SOURCE].decode("utf-8-sig"), contents[GUIDE_TEMPLATE].decode("utf-8"))
    if contents[GUIDE_OUTPUT] != expected.encode("utf-8"):
        raise ValueError("六步HTML与Markdown或模板不一致，请先重新生成")
    for name, raw in contents.items():
        safe_name(name)
        body = raw.decode("utf-8-sig")
        for label, pattern in PRIVATE:
            if pattern.search(body):
                errors.append(f"{name}：检测到{label}，请人工脱敏后重试")
        if name.endswith(".py"):
            ast.parse(body, filename=name)
        elif name.endswith(".json"):
            json.loads(body)
        elif name == FLOWCHART:
            validate_flowchart(body)
        if not name.endswith(".md"):
            continue
        for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", body):
            target = unquote(match.group(1).strip().strip("<>").split("#", 1)[0])
            if not target or target.startswith(("https://", "http://")):
                continue
            parts = list(PurePosixPath(name).parent.parts)
            escaped = False
            for part in target.split("/"):
                if part == "..":
                    if not parts:
                        escaped = True
                        break
                    parts.pop()
                elif part not in ("", "."):
                    parts.append(part)
            if escaped or target.startswith("/") or "/".join(parts) not in contents:
                errors.append(f"{name}：分享包内链接目标缺失：{target}")
    if errors:
        raise ValueError("\n".join(errors))


def inventory(contents):
    return {"schema_version": 1, "files": [{"path": name, "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in sorted(contents.items())]}


def verify_zip(path, expected_names):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected_names) | {INVENTORY}:
            raise ValueError("压缩包与白名单不一致或含重复条目")
        if any(row.file_size > LIMIT for row in archive.infolist()):
            raise ValueError("压缩包条目超过 2 MiB 限制")
        contents = {name: archive.read(name) for name in expected_names}
        validate_contents(contents)
        if json.loads(archive.read(INVENTORY)) != inventory(contents):
            raise ValueError("压缩包内容与内置 SHA256 清单不一致")
    return {"status": "ok", "file_count": len(contents), "zip_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def build_zip(root, target):
    root, target = root.resolve(), target.resolve()
    allowed = root / "dist"
    if target.parent != allowed or allowed.resolve() != allowed or target.suffix.lower() != ".zip":
        raise ValueError("打包目标必须是工具包 dist 目录下的 .zip 文件，禁止目录联接")
    if target.exists():
        raise ValueError("分享包已存在；使用新名称，禁止覆盖")
    contents = read_files(root)
    validate_contents(contents)
    target.parent.mkdir(parents=True, exist_ok=True)
    # 独占创建；源文件只读。中途失败的文件保留供检查，不自动删除。
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
        archive.writestr(INVENTORY, json.dumps(inventory(contents), ensure_ascii=False, indent=2).encode("utf-8"))
    return verify_zip(target, contents)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "pack", "verify"])
    parser.add_argument("--zip", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            if args.zip:
                parser.error("check 不接受 --zip")
            contents = read_files(ROOT)
            validate_contents(contents)
            result = {"status": "ok", "file_count": len(contents), "total_bytes": sum(map(len, contents.values()))}
        else:
            if args.zip is None:
                parser.error("pack 和 verify 必须提供 --zip")
            target = args.zip if args.zip.is_absolute() else ROOT / args.zip
            result = build_zip(ROOT, target) if args.command == "pack" else verify_zip(target, read_manifest(ROOT))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, SyntaxError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
