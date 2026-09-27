"""验证同源生成、六步结构与HTML转义，不启动浏览器或三维软件。"""
import importlib
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/通用"))
GUIDE = importlib.import_module("生成六步指南")
SHARE = importlib.import_module("分享工具包")


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.hrefs, self.external, self.panels, self.open_details = [], [], [], [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "section" and "step" in values.get("class", "").split():
            self.panels.append(values)
        if tag == "details" and "open" in values:
            self.open_details.append(values)
        if "id" in values:
            self.ids.append(values["id"])
        if "href" in values:
            self.hrefs.append(values["href"])
        if "src" in values or tag in ("iframe", "object", "embed", "form"):
            self.external.append((tag, values))


class GuideTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / GUIDE.SOURCE).read_text(encoding="utf-8")
        self.template = (ROOT / GUIDE.TEMPLATE).read_text(encoding="utf-8")
        self.page = GUIDE.render(self.source, self.template)

    def test_six_steps_have_all_fields(self):
        _, _, steps, extras = GUIDE.parse_source(self.source)
        self.assertEqual(len(steps), 6)
        self.assertEqual(len(extras), 3)
        for index, step in enumerate(steps, 1):
            self.assertEqual(list(step["values"]), GUIDE.FIELDS + (["修改意见模板"] if index in (4, 6) else []))

    def test_generated_file_is_current(self):
        self.assertEqual((ROOT / GUIDE.OUTPUT).read_bytes(), self.page.encode())

    def test_no_external_resources_and_local_navigation(self):
        parsed = PageParser()
        parsed.feed(self.page)
        self.assertFalse(parsed.external)
        self.assertEqual(len(parsed.ids), len(set(parsed.ids)))
        for href in parsed.hrefs:
            if href.startswith("#"):
                self.assertIn(href[1:], parsed.ids)
            else:
                self.assertEqual(href, Path(GUIDE.SOURCE).name)
        self.assertIn("connect-src 'none'", self.page)

    def test_untrusted_source_html_is_escaped(self):
        payload = '<img src=x onerror="bad()">'
        page = GUIDE.render(self.source.replace("一次交齐", payload), self.template)
        self.assertNotIn(payload, page)
        self.assertIn("&lt;img", page)

    def test_incomplete_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            GUIDE.render(self.source.replace("### 怎样算完成", "### 未知字段", 1), self.template)

    def test_reordered_step_is_rejected(self):
        with self.assertRaises(ValueError):
            GUIDE.parse_source(self.source.replace("## 01 ", "## 09 ", 1))

    def test_stale_html_blocks_sharing(self):
        contents = SHARE.read_files(ROOT)
        contents[GUIDE.OUTPUT] += b"\n"
        with self.assertRaisesRegex(ValueError, "不一致"):
            SHARE.validate_contents(contents)

    def test_other_html_is_not_allowed(self):
        with self.assertRaises(ValueError):
            SHARE.safe_name("docs/任意页面.html")

    def test_all_step_copy_prompts_are_present(self):
        _, _, steps, _ = GUIDE.parse_source(self.source)
        from html import escape
        for index, step in enumerate(steps, 1):
            self.assertIn(f'id="prompt-{index}"', self.page)
            self.assertIn(escape(step["prompt"]), self.page)
        self.assertEqual(len(re.findall('class="step gate-step"', self.page)), 2)

    def test_only_first_step_is_visible_by_default(self):
        parsed = PageParser()
        parsed.feed(self.page)
        self.assertEqual(len(parsed.panels), 6)
        self.assertEqual([p["id"] for p in parsed.panels if "hidden" not in p], ["step-1"])
        self.assertFalse(parsed.open_details)

    def test_no_progress_tracking_and_gate_revision_prompts(self):
        self.assertNotIn("localStorage", self.page)
        self.assertNotIn('type="checkbox"', self.page)
        self.assertIn('id="changes-4"', self.page)
        self.assertIn('id="changes-6"', self.page)
        self.assertEqual(self.page.count('>复制修改意见</button>'), 2)

    def test_default_tasks_are_capped_at_three(self):
        _, _, steps, _ = GUIDE.parse_source(self.source)
        for step in steps:
            self.assertLessEqual(len([line for line in step["values"]["您要做什么"].splitlines() if line.startswith("- ")]), 3)
        with self.assertRaises(ValueError):
            GUIDE.render(self.source.replace("### 您要做什么\n", "### 您要做什么\n\n- 多余事项\n", 1), self.template)


if __name__ == "__main__":
    unittest.main(verbosity=2)
