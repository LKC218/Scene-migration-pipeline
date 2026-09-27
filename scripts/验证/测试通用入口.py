"""不启动三维软件，验证通用配置、安全预览及分享包的独立运行。"""

from __future__ import annotations

import contextlib
import copy
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/通用"))
CONFIG = importlib.import_module("项目配置")
ENTRY = importlib.import_module("项目入口")
SHARE = importlib.import_module("分享工具包")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="工具包配置测试-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "输入").mkdir()
        (self.root / "输入/示例.max").touch()
        (self.root / "软件").mkdir()
        (self.root / "软件/3dsmaxbatch.exe").touch()
        self.data = json.loads((ROOT / "configs/项目配置.example.json").read_text(encoding="utf-8"))
        self.data.update(scene="输入/示例.max", max_batch="软件/3dsmaxbatch.exe")
        self.config = self.root / "配置.local.json"

    def load(self):
        self.config.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        return CONFIG.load_config(self.config, self.root)

    def inspect(self):
        with patch.object(CONFIG.os, "name", "nt"):
            return CONFIG.inspect_config(self.load(), self.root)

    def test_valid_config_does_not_create_output(self):
        result = self.inspect()
        self.assertEqual(result["status"], "ready_for_audit")
        self.assertFalse((self.root / "output").exists())

    def test_relative_path_is_root_based(self):
        self.assertEqual(CONFIG.resolve_path("输入/示例.max", self.root), self.root / "输入/示例.max")

    def test_unknown_field_is_rejected(self):
        self.data["command"] = "未知程序"
        with self.assertRaises(ValueError):
            self.load()

    def test_boolean_type_is_strict(self):
        self.data["material_details"] = "false"
        with self.assertRaises(ValueError):
            self.load()

    def test_schema_boolean_is_rejected(self):
        self.data["schema_version"] = True
        with self.assertRaises(ValueError):
            self.load()

    def test_duplicate_json_keys_are_rejected(self):
        self.config.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            CONFIG.load_config(self.config, self.root)

    def test_empty_scene_blocks(self):
        self.data["scene"] = ""
        self.assertEqual(self.inspect()["status"], "blocked")

    def test_missing_executable_blocks(self):
        self.data["max_batch"] = "软件/不存在.exe"
        self.assertEqual(self.inspect()["status"], "blocked")

    def test_existing_output_blocks(self):
        (self.root / self.data["output_dir"]).mkdir(parents=True)
        self.assertEqual(self.inspect()["status"], "blocked")

    def test_output_outside_batch_root_blocks(self):
        for target in ("../逃逸", "output/过程资产", "输入/输出"):
            with self.subTest(target=target):
                self.data["output_dir"] = target
                self.assertEqual(self.inspect()["status"], "blocked")

    def test_overlapping_protection_blocks(self):
        self.data["protect"] = ["."]
        self.assertEqual(self.inspect()["status"], "blocked")

    def test_missing_protection_blocks(self):
        self.data["protect"] = ["不存在"]
        self.assertEqual(self.inspect()["status"], "blocked")

    def test_arbitrary_environment_is_rejected(self):
        self.data["plugin_environment"] = {"PYTHONPATH": "软件"}
        with self.assertRaises(ValueError):
            self.load()

    def test_plugin_paths_are_resolved_without_mutating_environment(self):
        before = dict(os.environ)
        self.data["plugin_environment"] = {"ADSK_APPLICATION_PLUGINS": "软件"}
        result = self.inspect()
        self.assertEqual(result["plugin_environment"]["ADSK_APPLICATION_PLUGINS"], str(self.root / "软件"))
        self.assertEqual(dict(os.environ), before)

    def test_non_windows_blocks(self):
        data = self.load()
        with patch.object(CONFIG.os, "name", "posix"):
            self.assertEqual(CONFIG.inspect_config(data, self.root)["status"], "blocked")

    def test_output_directory_link_is_rejected(self):
        target = self.root / "实际目录"
        target.mkdir()
        try:
            (self.root / "output").symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"当前系统不允许创建测试符号链接：{exc}")
        self.assertEqual(self.inspect()["status"], "blocked")

    def run_entry(self, args, code=0):
        data, inspection = self.load(), self.inspect()
        with patch.object(ENTRY, "load_config", return_value=data), \
             patch.object(ENTRY, "inspect_config", return_value=inspection), \
             patch.object(ENTRY.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()):
            run.return_value.returncode = code
            result = ENTRY.main(args)
        return result, run

    def test_check_plan_and_default_audit_never_launch(self):
        for command in ("check", "plan", "audit"):
            result, run = self.run_entry([command])
            self.assertEqual(result, 0)
            run.assert_not_called()
        self.assertFalse((self.root / "output").exists())

    def test_explicit_execution_uses_array_and_preserves_exit_code(self):
        result, run = self.run_entry(["audit", "--execute"], code=7)
        self.assertEqual(result, 7)
        self.assertIsInstance(run.call_args.args[0], list)
        self.assertIn("--material-details", run.call_args.args[0])
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_blocked_config_never_launches(self):
        self.data["scene"] = ""
        result, run = self.run_entry(["audit", "--execute"])
        self.assertEqual(result, 2)
        run.assert_not_called()

    def test_execute_flag_is_not_valid_on_plan(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ENTRY.main(["plan", "--execute"])


class ShareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="工具包分享测试-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "独立 中文目录"
        self.root.mkdir()
        self.contents = SHARE.read_files(ROOT)
        for name, raw in self.contents.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)

    def test_share_content_has_no_assets_or_local_config(self):
        SHARE.validate_contents(self.contents)
        self.assertFalse(any(name.startswith(("源工程/", "output/")) or name.endswith(".local.json") for name in self.contents))

    def test_disallowed_names_are_rejected(self):
        for name in ("../秘密.md", "output/报告.md", "configs/项目.local.json", "模型.blend", "./README.md", 123):
            with self.subTest(name=name), self.assertRaises(ValueError):
                SHARE.safe_name(name)

    def test_git_rules_are_allowed_but_hidden_settings_are_not(self):
        self.assertEqual(SHARE.safe_name(".gitattributes"), ".gitattributes")
        self.assertEqual(SHARE.safe_name(".gitignore"), ".gitignore")
        with self.assertRaises(ValueError):
            SHARE.safe_name(".git/config")
        with self.assertRaises(ValueError):
            SHARE.safe_name(".env")

    def test_broken_portable_link_is_rejected(self):
        contents = copy.copy(self.contents)
        contents["README.md"] = b"[missing](missing.md)"
        with self.assertRaises(ValueError):
            SHARE.validate_contents(contents)

    def test_only_named_flowchart_is_allowed(self):
        self.assertEqual(SHARE.safe_name(SHARE.FLOWCHART), SHARE.FLOWCHART)
        with self.assertRaises(ValueError):
            SHARE.safe_name("docs/图示/其他.svg")

    def test_flowchart_active_content_is_rejected(self):
        for content in ('<script>bad()</script>', '<foreignObject/>',
                        '<rect onclick="bad()"/>', '<image href="https://example.invalid/a"/>',
                        '<rect fill="url(https://example.invalid/a)"/>'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                SHARE.validate_flowchart('<svg xmlns="http://www.w3.org/2000/svg">' + content + '</svg>')

    def test_flowchart_declarations_are_rejected(self):
        for prefix in ('<!DOCTYPE svg>', '<!ENTITY test "value">', '<?xml-stylesheet href="remote"?>'):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                SHARE.validate_flowchart(prefix + '<svg xmlns="http://www.w3.org/2000/svg"/>')

    def test_flowchart_and_artist_guides_are_packaged(self):
        SHARE.validate_flowchart(self.contents[SHARE.FLOWCHART].decode("utf-8"))
        self.assertIn("docs/使用指南/场景迁移与优化-美术操作流程.md", self.contents)
        self.assertIn("docs/执行规范/场景迁移与优化-Agent执行约定.md", self.contents)

    def test_absolute_machine_path_is_rejected(self):
        contents = copy.copy(self.contents)
        contents["README.md"] = ("Z" + ":/" + "private/data").encode()
        with self.assertRaises(ValueError):
            SHARE.validate_contents(contents)

    def test_pack_roundtrip_and_no_overwrite(self):
        target = self.root / "dist/工具包.zip"
        result = SHARE.build_zip(self.root, target)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["file_count"], len(self.contents))
        with self.assertRaises(ValueError):
            SHARE.build_zip(self.root, target)

    def test_pack_outside_dist_is_rejected(self):
        with self.assertRaises(ValueError):
            SHARE.build_zip(self.root, self.root / "外部.zip")

    def test_linked_share_file_is_rejected(self):
        target = self.root / "原README.md"
        original = self.root / "README.md"
        original.rename(target)
        try:
            original.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"当前系统不允许创建测试符号链接：{exc}")
        with self.assertRaises(ValueError):
            SHARE.read_files(self.root)

    def test_unlisted_local_files_are_not_packaged(self):
        (self.root / "configs/私有.local.json").write_text("{}", encoding="utf-8")
        (self.root / "源工程").mkdir()
        (self.root / "源工程/秘密.max").touch()
        target = self.root / "dist/白名单.zip"
        SHARE.build_zip(self.root, target)
        with zipfile.ZipFile(target) as archive:
            self.assertNotIn("configs/私有.local.json", archive.namelist())
            self.assertNotIn("源工程/秘密.max", archive.namelist())

    def test_tampered_archive_is_rejected(self):
        target = self.root / "篡改.zip"
        with zipfile.ZipFile(target, "w") as archive:
            for name, raw in self.contents.items():
                archive.writestr(name, raw + b"\n" if name == "README.md" else raw)
            archive.writestr(SHARE.INVENTORY, json.dumps(SHARE.inventory(self.contents)))
        with self.assertRaises(ValueError):
            SHARE.verify_zip(target, self.contents)

    def test_independent_directory_without_old_assets(self):
        foreign_cwd = Path(self.temp.name)
        commands = [
            ([str(self.root / "scripts/通用/分享工具包.py"), "check"], 0),
            ([str(self.root / "scripts/通用/项目入口.py"), "check", "--config", "configs/项目配置.example.json"], 2),
        ]
        for args, expected in commands:
            result = subprocess.run([sys.executable, "-X", "utf8", "-B", *args], cwd=foreign_cwd,
                                    capture_output=True, text=True, encoding="utf-8", timeout=30,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertFalse((self.root / "output").exists())
        self.assertFalse((self.root / "源工程").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
