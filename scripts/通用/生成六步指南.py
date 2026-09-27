"""从固定结构的六步Markdown生成离线HTML；只使用标准库，不启动三维软件。"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "docs/使用指南/场景迁移与优化-六步指南.md"
TEMPLATE = "scripts/通用/模板/六步指南页面.html"
OUTPUT = "docs/使用指南/场景迁移与优化-六步指南.html"
FIELDS = ["您要做什么", "Agent会做什么", "怎样算完成", "不通过怎么办", "复制给Agent"]
EXTRAS = ["最终会拿到什么", "常见问题", "Agent执行底线"]


def blocks(body):
    """仅渲染本指南约定的段落、三级标题、列表和引用；原始HTML永不执行。"""
    rows = []
    list_open = False
    for line in body.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("- "):
            if not list_open:
                rows.append("<ul>")
                list_open = True
            rows.append("<li>" + html.escape(line[2:]) + "</li>")
            continue
        if list_open:
            rows.append("</ul>")
            list_open = False
        if line.startswith("### "):
            rows.append("<h3>" + html.escape(line[4:]) + "</h3>")
        else:
            rows.append("<p>" + html.escape(line.removeprefix("> ")) + "</p>")
    if list_open:
        rows.append("</ul>")
    return "\n".join(rows)


def parse_source(markdown):
    markdown = markdown.replace("\r\n", "\n")
    pieces = re.split(r"^## (.+)$", markdown, flags=re.MULTILINE)
    intro = pieces[0].strip().splitlines()
    if not intro or not intro[0].startswith("# "):
        raise ValueError("指南缺少一级标题")
    sections = list(zip(pieces[1::2], pieces[2::2]))
    if len(sections) != 9 or [title for title, _ in sections[6:]] != EXTRAS:
        raise ValueError("指南必须包含六步及三个约定的附录")
    steps = []
    for index, (title, body) in enumerate(sections[:6], 1):
        if not title.startswith(f"{index:02d} "):
            raise ValueError("六步必须按01到06排列")
        parts = re.split(r"^### (.+)$", body, flags=re.MULTILINE)
        expected_fields = FIELDS + (["修改意见模板"] if index in (4, 6) else [])
        if parts[1::2] != expected_fields or any(not value.strip() for value in parts[2::2]):
            raise ValueError(f"第{index}步的说明栏目不完整")
        values = dict(zip(parts[1::2], parts[2::2]))
        prompt = values["复制给Agent"].strip()
        if not prompt.startswith("> "):
            raise ValueError("复制话术必须以引用段落书写")
        changes = values.get("修改意见模板", "").strip()
        if changes and not changes.startswith("> "):
            raise ValueError("修改意见必须以引用段落书写")
        steps.append({"title": title[3:], "lead": parts[0].strip(), "values": values,
                      "changes": "\n".join(line.removeprefix("> ") for line in changes.splitlines()),
                      "prompt": "\n".join(line.removeprefix("> ") for line in prompt.splitlines())})
    return intro[0][2:], "\n".join(intro[1:]), steps, sections[6:]


def render(markdown, template):
    markdown = markdown.replace("\r\n", "\n")
    title, intro, steps, extras = parse_source(markdown)
    navigation, content = [], []
    for i, step in enumerate(steps, 1):
        gate = i in (4, 6)
        badge = '<span class="gate-tag">看图确认</span>' if gate else ""
        navigation.append(f'<button type="button" data-go="{i}" aria-controls="step-{i}" disabled><span>{i:02d}</span>{html.escape(step["title"])}</button>')
        user_items = step["values"]["您要做什么"]
        if len([line for line in user_items.splitlines() if line.startswith("- ")]) > 3:
            raise ValueError("美术人员默认事项最多三条；其余内容放入详细说明")
        secondary = (f'<button type="button" class="secondary" data-copy="changes-{i}" disabled>复制修改意见</button>' if gate else "")
        changes = (f'<h3>修改意见</h3><blockquote id="changes-{i}">{html.escape(step["changes"])}</blockquote>' if gate else "")
        more = "".join(f'<h3>{name}</h3>{blocks(step["values"][name])}' for name in FIELDS[1:4])
        content.append(f'''<section class="step{' gate-step' if gate else ''}" id="step-{i}" aria-labelledby="heading-{i}" {'hidden' if i != 1 else ''}>
<div class="step-top"><span>第 {i:02d} 步 / 06</span>{badge}</div>
<h2 id="heading-{i}" tabindex="-1">{html.escape(step['title'])}</h2><p class="lead">{html.escape(step['lead'])}</p>
<div class="user-tasks">{blocks(user_items)}</div>
<div class="actions"><button type="button" data-copy="prompt-{i}" disabled>{'复制通过回复' if gate else '复制给 Agent'}</button>{secondary}</div>
<p class="feedback" id="feedback-{i}" role="status" aria-live="polite"></p>
<div class="copy-fallback" hidden><label for="fallback-{i}">请手动复制下方已选文字</label><textarea id="fallback-{i}" readonly rows="5"></textarea></div>
<details class="disclosure"><summary>查看完整话术</summary><div><blockquote id="prompt-{i}">{html.escape(step['prompt'])}</blockquote>{changes}</div></details>
<details class="disclosure"><summary>Agent 怎么做 · 完成标准 · 遇到问题</summary><div>{more}</div></details>
</section>''')
    extras_html = "".join(f'<details class="extra"><summary>{html.escape(name)}<span aria-hidden="true">＋</span></summary><div>{blocks(body)}</div></details>' for name, body in extras)
    replacements = {"__TITLE__": html.escape(title), "__INTRO__": blocks(intro),
                    "__NAV__": "\n".join(navigation), "__STEPS__": "\n".join(content),
                    "__EXTRAS__": extras_html, "__SOURCE_HASH__": hashlib.sha256(markdown.encode()).hexdigest(),
                    "__MD_NAME__": Path(SOURCE).name}
    for token in replacements:
        if token not in template:
            raise ValueError(f"页面模板缺少占位：{token}")
    # 单次替换，避免源文字碰巧含模板标记时被二次解释。
    return re.sub("|".join(map(re.escape, replacements)), lambda match: replacements[match.group()], template)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="仅检查HTML是否与源文档及模板一致")
    args = parser.parse_args()
    try:
        expected = render((ROOT / SOURCE).read_text(encoding="utf-8-sig"), (ROOT / TEMPLATE).read_text(encoding="utf-8"))
        output = ROOT / OUTPUT
        if args.check:
            if not output.is_file() or output.read_bytes() != expected.encode("utf-8"):
                raise ValueError("HTML未生成或已过期，请重新生成；禁止只修改HTML正文")
            print("六步指南HTML与Markdown、模板一致")
        else:
            output.write_text(expected, encoding="utf-8", newline="\n")
            print(f"已生成：{OUTPUT}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"生成检查失败：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
