"""Build Lark interactive card (schema 2.0) from digest.json.

Each section shows up to VISIBLE items inline; any overflow goes into a native
collapsible_panel ("展开剩余 N 条") that expands client-side — no callback server
needed, which suits an incoming-webhook bot.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


SECTION_META = [
    ("frontier", "🤖 AI Agent / 大模型"),
    ("breaking", "🔥 今日重磅"),
    ("oversea", "🌏 海外实验室/公司动态"),
    ("cn", "🇨🇳 国内公司 & 行业动态"),
    ("trending", "🚀 GitHub Trending"),
]

VISIBLE = 5  # items shown before the "展开剩余" fold


def _fmt_item(it: dict, idx: int) -> str:
    title = (it.get("title") or "").strip()
    summary = (it.get("summary") or "").strip()
    url = (it.get("url") or "").strip()
    source = (it.get("source") or "").strip()
    head = f"**{idx}. [{title}]({url})**" if url else f"**{idx}. {title}**"
    parts = [head]
    if summary:
        parts.append(summary)
    if source:
        parts.append(f"<font color='grey'>— {source}</font>")
    return "\n".join(parts)


def _md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _collapsible(title: str, elements: list[dict]) -> dict:
    """Native client-side fold. Collapsed by default."""
    return {
        "tag": "collapsible_panel",
        "expanded": False,
        "header": {
            "title": {"tag": "markdown", "content": title},
            "icon": {
                "tag": "standard_icon",
                "token": "down-small-ccm_outlined",
                "color": "grey",
                "size": "16px 16px",
            },
            "icon_position": "right",
            "icon_expanded_angle": 180,
        },
        "elements": elements,
    }


def build_card(digest: dict) -> dict:
    date = digest.get("date") or datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    sections = digest.get("sections") or {}
    meta = digest.get("meta") or {}
    label = (digest.get("_label") or "").strip()
    title = f"📡 AI 日报 · {date}" + (f" · {label}" if label else "")

    elements: list[dict] = []
    first = True
    for key, name in SECTION_META:
        items = sections.get(key) or []
        if not items:
            continue
        if not first:
            elements.append({"tag": "hr"})
        first = False
        elements.append(_md(f"**{name}**（{len(items)}）"))

        for i, it in enumerate(items[:VISIBLE], 1):
            elements.append(_md(_fmt_item(it, i)))

        rest = items[VISIBLE:]
        if rest:
            hidden = [_md(_fmt_item(it, i)) for i, it in enumerate(rest, VISIBLE + 1)]
            elements.append(_collapsible(f"**展开剩余 {len(rest)} 条 ▾**", hidden))

    if not elements:
        elements.append(_md("_今日无符合筛选条件的条目_"))

    ok = meta.get("ok_sources", "?")
    total = meta.get("total_sources", "?")
    cst_now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%H:%M CST")
    elements.append({"tag": "hr"})
    elements.append(_md(f"<font color='grey'>数据源 {ok}/{total} 正常 · 生成于 {cst_now}</font>"))

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "body": {"elements": elements},
        },
    }


def build_text_message(text: str) -> dict:
    return {"msg_type": "text", "content": {"text": text}}
