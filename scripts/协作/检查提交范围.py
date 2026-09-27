"""从Git暂存区或提交对象检查范围；不执行候选代码，不删除或暂存文件。"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
POLICY = "configs/仓库提交白名单.json"
SHARE_LIST = "configs/分享白名单.json"
BASE = "refs/remotes/origin/main"
LIMIT = 2 * 1024 * 1024
DENIED_DIRS = {"源工程", "input", "assets-local", "output", "dist", "node_modules", "__pycache__",
               ".venv", "venv", "tmp", "案例记录", "历史归档", "参考图"}
DENIED_SUFFIXES = {".max", ".blend", ".fbx", ".glb", ".gltf", ".zip", ".7z", ".rar", ".exe", ".dll", ".log", ".pem", ".key"}
PATTERNS = [
    ("疑似私人绝对路径", re.compile(r"\b[A-Za-z]:[/\\]|/(?:Users|home)/[\w.-]+/")),
    ("疑似私钥", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("疑似访问凭据", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{20,})")),
    ("疑似明文口令", re.compile(r'''(?i)(?:api[_-]?key|password|access[_-]?token|secret)\s*[:=]\s*["'][A-Za-z0-9_+/=-]{12,}["']''')),
]


def git(repo, *args, input_data=None):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(["git", "-C", str(repo), *args], input=input_data, capture_output=True, env=env)
    if result.returncode:
        # 不打印Git正文或候选文件内容，防止错误日志再次泄露数据。
        raise ValueError(f"Git检查失败：{args[0]}；请确认引用存在、仓库完整且已获取origin/main")
    return result.stdout


def valid_path(name):
    if not isinstance(name, str) or not name or re.search(r"[\x00-\x1f\x7f:*?\\]", name):
        raise ValueError("白名单路径不合法")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != name:
        raise ValueError("白名单路径必须是规范相对路径")
    if any(part.casefold() in {item.casefold() for item in DENIED_DIRS} for part in path.parts) or path.suffix.lower() in DENIED_SUFFIXES:
        raise ValueError(f"禁止资产、缓存或凭据路径：{name}")
    if re.search(r"\.blend\d+$", name, re.I) or name.lower().endswith(".local.json") or path.name.lower().startswith(".env"):
        raise ValueError(f"禁止本机配置或备份：{name}")
    if any(part.startswith(".") for part in path.parts) and not (
            name in {".gitignore", ".gitattributes", ".github/CODEOWNERS", ".githooks/pre-commit", ".githooks/pre-push"}
            or name.startswith(".github/workflows/")):
        raise ValueError(f"隐藏路径未受支持：{name}")
    return name


def policy_from_bytes(raw):
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("files"), list):
        raise ValueError("仓库提交白名单格式错误")
    names = [valid_path(name) for name in data["files"]]
    if len(names) != len({name.casefold() for name in names}) or POLICY not in names:
        raise ValueError("仓库白名单有重复路径或缺少自身")
    return set(names)


def commit_id(repo, ref):
    if not ref or ref.startswith("-") or any(c.isspace() for c in ref):
        raise ValueError("Git引用不合法")
    return git(repo, "rev-parse", "--verify", ref + "^{commit}").decode().strip()


def load_policy(repo, ref):
    oid = commit_id(repo, ref)
    return policy_from_bytes(git(repo, "show", oid + ":" + POLICY))


def entries(repo, ref=None):
    result = {}
    if ref is None:
        raw = git(repo, "ls-files", "--stage", "-z")
        for line in raw.split(b"\0"):
            if not line:
                continue
            info, path = line.split(b"\t", 1)
            mode, oid, stage = info.decode().split()
            if stage != "0":
                raise ValueError("暂存区存在未解决冲突")
            result[path.decode("utf-8")] = (mode, oid)
    else:
        oid = commit_id(repo, ref)
        for line in git(repo, "ls-tree", "-r", "-z", oid).split(b"\0"):
            if not line:
                continue
            info, path = line.split(b"\t", 1)
            mode, kind, object_id = info.decode().split()
            result[path.decode("utf-8")] = (mode, object_id)
    return result


def inspect_snapshot(repo, snapshot, allowed, *, bundle=True):
    errors, contents = [], {}
    for name, (mode, oid) in snapshot.items():
        if name not in allowed:
            errors.append(f"不在已批准仓库白名单：{name}")
            continue
        try:
            valid_path(name)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if mode not in {"100644", "100755"}:
            errors.append(f"只允许普通文件，拒绝链接或子模块：{name}")
            continue
        if int(git(repo, "cat-file", "-s", oid)) > LIMIT:
            errors.append(f"超过2 MiB限制：{name}")
            continue
        raw = git(repo, "cat-file", "blob", oid)
        try:
            body = raw.decode("utf-8-sig")
            if "\0" in body:
                raise ValueError("二进制内容")
        except (UnicodeError, ValueError):
            errors.append(f"非UTF8文本或包含二进制内容：{name}")
            continue
        for label, pattern in PATTERNS:
            if pattern.search(body):
                errors.append(f"{label}：{name}（不输出命中内容）")
        try:
            if name.endswith(".py"):
                ast.parse(body, filename=name)
            elif name.endswith(".json"):
                json.loads(body)
        except (ValueError, SyntaxError):
            errors.append(f"语法或JSON解析失败：{name}")
        contents[name] = raw
    if POLICY not in contents:
        errors.append("提交树缺少仓库白名单")
    else:
        try:
            policy_from_bytes(contents[POLICY])
        except ValueError as exc:
            errors.append(str(exc))
    if bundle and not errors:
        try:
            # 仅导入正在执行的受信任工具，不导入候选提交中的任何代码。
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "通用"))
            import 分享工具包 as share
            manifest = json.loads(contents[SHARE_LIST])
            names = manifest["files"]
            if len(names) != len(set(names)) or any(name not in contents for name in names):
                raise ValueError("分享清单存在重复或缺失文件")
            share.validate_contents({name: contents[name] for name in names})
        except (ValueError, KeyError, TypeError, SyntaxError) as exc:
            # share错误只含路径和规则，不打印候选源码。
            errors.append(f"分享或生成检查失败：{exc}")
    return errors


def inspect_commits(repo, commits, allowed, *, bundle=True):
    errors = []
    for oid in commits:
        errors += [f"提交{oid[:12]}：{error}" for error in inspect_snapshot(repo, entries(repo, oid), allowed, bundle=bundle)]
    return errors


def range_commits(repo, base, head):
    base_oid, head_oid = commit_id(repo, base), commit_id(repo, head)
    git(repo, "merge-base", "--is-ancestor", base_oid, head_oid)
    commits = git(repo, "rev-list", "--reverse", base_oid + ".." + head_oid).decode().splitlines()
    return commits or [head_oid]


def check_push(repo, lines, *, bundle=True):
    allowed = load_policy(repo, BASE)
    errors, checked = [], set()
    for line in lines.splitlines():
        values = line.split()
        if len(values) != 4:
            raise ValueError("推送引用输入不完整")
        local_ref, local_oid, remote_ref, remote_oid = values
        if remote_ref == "refs/heads/main":
            errors.append("禁止直接推送或删除main，请使用合并请求")
            continue
        if not local_ref.startswith("refs/heads/") or not remote_ref.startswith("refs/heads/"):
            errors.append("当前检查入口仅支持工作分支，标签或删除须另行审核")
            continue
        if not all(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) for value in (local_oid, remote_oid)):
            raise ValueError("推送对象标识不合法")
        if set(local_oid) == {"0"}:
            errors.append("此入口不执行远程分支删除")
            continue
        # 工作分支必须包含本机已获取的主分支；远程CI还会按真正目标主分支复查。
        git(repo, "merge-base", "--is-ancestor", commit_id(repo, BASE), commit_id(repo, local_oid))
        start = BASE if set(remote_oid) == {"0"} else remote_oid
        try:
            checked.update(range_commits(repo, start, local_oid))
        except ValueError:
            errors.append("拒绝非快进推送或缺失远程基线；先获取远程并核对")
    errors += inspect_commits(repo, sorted(checked), allowed, bundle=bundle)
    return errors, len(checked)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["index", "range", "push"])
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--policy-ref", default=BASE)
    args = parser.parse_args(argv)
    try:
        repo = args.repo.resolve()
        if args.mode == "index":
            if git(repo, "branch", "--show-current").decode().strip() == "main":
                raise ValueError("请在工作分支提交，不直接修改main")
            errors = inspect_snapshot(repo, entries(repo), load_policy(repo, BASE))
            count = 1
        elif args.mode == "push":
            lines = sys.stdin.read()
            if not lines.strip():
                print("没有待推送引用")
                return 0
            errors, count = check_push(repo, lines)
        else:
            if not args.base:
                raise ValueError("范围检查必须显式指定--base")
            commits = range_commits(repo, args.base, args.head)
            errors = inspect_commits(repo, commits, load_policy(repo, args.policy_ref))
            count = len(commits)
        print(json.dumps({"status": "blocked" if errors else "ok", "snapshots": count, "errors": errors}, ensure_ascii=False, indent=2))
        return 1 if errors else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "blocked", "errors": [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
