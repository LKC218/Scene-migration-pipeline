"""在隔离临时Git仓库验证协作防护；不访问网络，不改全局配置。"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = load("repo_guard", "scripts/协作/检查提交范围.py")
SETUP = load("repo_setup", "scripts/协作/初始化协作环境.py")


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="提交检查测试-")
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.repo = Path(self.temp.name)
        self.g("init", "-b", "main")
        self.g("config", "user.name", "测试用户")
        self.g("config", "user.email", "test@example.invalid")
        self.g("config", "commit.gpgsign", "false")
        self.allowed = {"README.md", GUARD.POLICY, GUARD.SHARE_LIST, "docs/允许.md"}
        self.write("README.md", "# 测试\n")
        self.write(GUARD.POLICY, json.dumps({"schema_version": 1, "files": sorted(self.allowed)}))
        self.write(GUARD.SHARE_LIST, '{"files": ["README.md"]}')
        self.g("add", "--", "README.md", GUARD.POLICY, GUARD.SHARE_LIST)
        self.commit()
        self.g("update-ref", GUARD.BASE, "HEAD")
        self.g("switch", "-c", "work")

    def g(self, *args):
        return GUARD.git(self.repo, *args).decode().strip()

    def write(self, name, body):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))

    def commit(self):
        self.g("commit", "--allow-empty", "-m", "test: 验证")

    def errors(self):
        return GUARD.inspect_snapshot(self.repo, GUARD.entries(self.repo), GUARD.load_policy(self.repo, GUARD.BASE), bundle=False)

    def test_valid_index(self):
        self.assertEqual(self.errors(), [])

    def test_unlisted_file_blocks(self):
        self.write("无关.txt", "临时内容")
        self.g("add", "--", "无关.txt")
        self.assertTrue(any("不在" in error for error in self.errors()))

    def test_reads_staged_not_worktree(self):
        self.write("README.md", "Z" + ":/" + "private/data")
        self.g("add", "--", "README.md")
        self.write("README.md", "# 干净工作文件\n")
        self.assertTrue(any("私人" in error for error in self.errors()))

    def test_unstaged_noise_not_included(self):
        self.write("私人.tmp", "仅本地")
        self.assertEqual(self.errors(), [])

    def test_self_whitelisting_does_not_pass(self):
        self.write("新文件.md", "# 新文件\n")
        self.write(GUARD.POLICY, json.dumps({"schema_version": 1, "files": sorted(self.allowed | {"新文件.md"})}))
        self.g("add", "--", GUARD.POLICY, "新文件.md")
        self.assertTrue(any("新文件.md" in error for error in self.errors()))

    def test_policy_proposal_without_payload_passes(self):
        self.write(GUARD.POLICY, json.dumps({"schema_version": 1, "files": sorted(self.allowed | {"新文件.md"})}))
        self.g("add", "--", GUARD.POLICY)
        self.assertEqual(self.errors(), [])

    def test_large_blob_blocks(self):
        self.write("README.md", b"a" * (GUARD.LIMIT + 1))
        self.g("add", "--", "README.md")
        self.assertTrue(any("2 MiB" in error for error in self.errors()))

    def test_binary_blocks(self):
        self.write("README.md", b"text\x00binary")
        self.g("add", "--", "README.md")
        self.assertTrue(any("二进制" in error for error in self.errors()))

    def test_secret_is_redacted(self):
        value = "ghp_" + "a" * 40
        self.write("README.md", value)
        self.g("add", "--", "README.md")
        errors = self.errors()
        self.assertTrue(any("凭据" in error for error in errors))
        self.assertNotIn(value, "\n".join(errors))

    def test_private_key_blocks(self):
        self.write("README.md", "-----BEGIN " + "PRIVATE KEY" + "-----")
        self.g("add", "--", "README.md")
        self.assertTrue(any("私钥" in error for error in self.errors()))

    def test_sensitive_assignment_blocks(self):
        self.write("README.md", 'pass' + 'word = "' + 'a' * 16 + '"')
        self.g("add", "--", "README.md")
        self.assertTrue(any("口令" in error for error in self.errors()))

    def test_asset_paths_cannot_be_whitelisted(self):
        for name in ("output/file.md", "Output/file.md", "模型.max", "x.LOCAL.JSON", ".env", "docs/../other.md", "*.md"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                GUARD.valid_path(name)

    def test_removing_unlisted_tracked_file_is_allowed(self):
        self.write("无关.txt", "旧误提交")
        self.g("add", "--", "无关.txt")
        self.commit()
        self.g("rm", "--cached", "--", "无关.txt")
        self.assertEqual(self.errors(), [])
        self.assertTrue((self.repo / "无关.txt").exists())

    def test_add_then_delete_is_caught_in_push_history(self):
        self.write("无关.txt", "旧误提交")
        self.g("add", "--", "无关.txt")
        self.commit()
        self.g("rm", "--cached", "--", "无关.txt")
        self.commit()
        oid = self.g("rev-parse", "HEAD")
        errors, count = GUARD.check_push(self.repo, f"refs/heads/work {oid} refs/heads/work {'0' * 40}", bundle=False)
        self.assertEqual(count, 2)
        self.assertTrue(any("无关.txt" in error for error in errors))

    def test_push_to_main_blocks(self):
        oid = self.g("rev-parse", "HEAD")
        errors, _ = GUARD.check_push(self.repo, f"refs/heads/work {oid} refs/heads/main {'0' * 40}", bundle=False)
        self.assertTrue(any("main" in error for error in errors))

    def test_regular_branch_push_passes(self):
        self.write("README.md", "# 修改\n")
        self.g("add", "--", "README.md")
        self.commit()
        oid = self.g("rev-parse", "HEAD")
        errors, count = GUARD.check_push(self.repo, f"refs/heads/work {oid} refs/heads/work {'0' * 40}", bundle=False)
        self.assertEqual(errors, [])
        self.assertEqual(count, 1)

    def test_main_commit_cli_blocks(self):
        self.g("switch", "main")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(GUARD.main(["index", "--repo", str(self.repo)]), 1)

    def test_link_mode_blocks(self):
        snapshot = GUARD.entries(self.repo)
        mode, oid = snapshot["README.md"]
        snapshot["README.md"] = ("120000", oid)
        self.assertTrue(any("链接" in error for error in GUARD.inspect_snapshot(self.repo, snapshot, self.allowed, bundle=False)))

    def test_existing_hook_configuration_is_preserved(self):
        self.g("config", "core.hooksPath", "custom-hooks")
        with self.assertRaises(ValueError):
            SETUP.configure(self.repo, True)
        self.assertEqual(self.g("config", "core.hooksPath"), "custom-hooks")

    def test_existing_default_hook_is_preserved(self):
        self.write(".git/hooks/pre-commit", "#!/bin/sh\nexit 0\n")
        with self.assertRaises(ValueError):
            SETUP.configure(self.repo, True)
        self.assertTrue((self.repo / ".git/hooks/pre-commit").exists())

    def test_full_bundle_and_installed_hook(self):
        policy = json.loads((ROOT / GUARD.POLICY).read_text(encoding="utf-8"))
        for name in policy["files"]:
            self.write(name, (ROOT / name).read_bytes())
        for name in SETUP.HOOKS:
            (self.repo / ".githooks" / name).chmod(0o755)
        self.g("add", "--", *policy["files"])
        self.g("update-index", "--chmod=+x", ".githooks/pre-commit", ".githooks/pre-push")
        self.commit()
        self.g("update-ref", GUARD.BASE, "HEAD")
        self.assertEqual(SETUP.configure(self.repo, True)["status"], "ok")
        self.assertEqual(SETUP.configure(self.repo, False)["status"], "ok")
        self.write("README.md", (self.repo / "README.md").read_text(encoding="utf-8") + "\n测试说明。\n")
        self.g("add", "--", "README.md")
        self.commit()  # 真正触发临时仓库的pre-commit。
        self.write("无关.txt", "阻塞测试")
        self.g("add", "--", "无关.txt")
        result = subprocess.run(["git", "-C", str(self.repo), "commit", "-m", "test: 应被阻止"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.repo / "无关.txt").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
