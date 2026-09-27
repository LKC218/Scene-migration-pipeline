"""通用安全审计入口：检查、预览、显式执行；不自动调用案例转换脚本。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from 项目配置 import ROOT, inspect_config, load_config


def make_plan(data, inspection, root=ROOT):
    paths = inspection["paths"]
    command = [sys.executable, "-X", "utf8", "-B", str(root / "scripts/诊断/运行独立审计.py"),
               "--scene", paths["scene"], "--max-batch", paths["max_batch"], "--output", paths["output_dir"]]
    for path in inspection["protect"][1:]:
        command += ["--protect", path]
    if data["material_details"]:
        command.append("--material-details")
    return {"project_name": data["project_name"], **inspection, "command": command,
            "safe_scene": True, "conversion_performed": False,
            "boundary": "仅审计，不导出、不改源工程、不安装插件、不连接 Unity；审计无阻塞也需材质映射评估"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "plan", "audit"])
    parser.add_argument("--config", default="configs/项目配置.local.json")
    parser.add_argument("--execute", action="store_true", help="仅 audit 可用；显式启动 Max 安全审计")
    args = parser.parse_args(argv)
    if args.execute and args.command != "audit":
        parser.error("--execute 只能与 audit 一起使用")
    try:
        data = load_config(args.config)
        inspection = inspect_config(data)
        result = inspection if args.command == "check" else make_plan(data, inspection)
        result["execution_requested"] = bool(args.execute)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        if inspection["errors"]:
            return 2
        if args.command != "audit" or not args.execute:
            return 0
        # 插件变量仅覆盖子进程；不持久化用户级或系统级设置。
        env = os.environ.copy()
        env.update(inspection["plugin_environment"])
        completed = subprocess.run(result["command"], cwd=ROOT, env=env,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        return completed.returncode
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "conversion_performed": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
