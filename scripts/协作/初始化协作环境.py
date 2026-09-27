"""安装或只读核对本仓库钩子；遇到其他钩子不覆盖，不修改全局Git设置。"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ("pre-commit", "pre-push")


def run(repo, *args):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env)


def configure(repo, install=False):
    if run(repo, "rev-parse", "--show-toplevel").returncode:
        raise ValueError("当前目录不是Git仓库")
    hooks_config = run(repo, "config", "--get", "core.hooksPath")
    configured = hooks_config.stdout.decode().strip()
    if hooks_config.returncode == 0 and configured != ".githooks":
        raise ValueError("存在其他钩子目录，请人工核对整合；未覆盖")
    git_dir = Path(run(repo, "rev-parse", "--absolute-git-dir").stdout.decode().strip())
    if not configured and any((git_dir / "hooks" / name).exists() for name in HOOKS):
        raise ValueError("默认目录已有活动钩子，请人工核对；未覆盖")
    for name in HOOKS:
        path = repo / ".githooks" / name
        approved = run(repo, "show", "refs/remotes/origin/main:.githooks/" + name)
        if approved.returncode or not path.is_file() or path.read_bytes() != approved.stdout:
            raise ValueError("钩子文件与已获取的origin/main不一致；先获取远程并核对")
        if os.name != "nt" and not os.access(path, os.X_OK):
            raise ValueError("钩子缺少可执行权限，请核对Git文件模式")
    executable = Path(sys.executable).as_posix()
    current_python = run(repo, "config", "--local", "--get", "sceneGuard.python").stdout.decode().strip()
    if install:
        for key, value in (("sceneGuard.python", executable), ("core.hooksPath", ".githooks")):
            if run(repo, "config", "--local", key, value).returncode:
                raise ValueError("写入本地Git配置失败，请重新检查")
    elif configured != ".githooks" or current_python != executable:
        raise ValueError("本仓库未为当前Python安装协作钩子；请执行install")
    return {"status": "ok", "scope": "仅当前克隆的本地Git配置", "hooks": list(HOOKS)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "check"])
    args = parser.parse_args()
    try:
        print(json.dumps(configure(ROOT, args.action == "install"), ensure_ascii=False))
    except (OSError, ValueError) as exc:
        print(str(exc))
        raise SystemExit(1)
