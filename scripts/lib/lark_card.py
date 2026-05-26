"""Build Lark interactive card from digest.json."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


SECTION_META = [
    ("breaking", "🔥 今日重磅"),
    ("oversea", "🌏 海外实验室/公司动态"),
    ("cn", "🇨🇳 国内公司 & 行业动态"),
    ("hot", "📈 热榜"),
]


def _fmt_item(it: dict) -> str:
    title = (it.get("title") or "").strip()
    summary = (it.get("summary") or "").strip()
    url = (it.get("url") or "").strip()
    source = (it.get("source") or "").strip()
    head = f"**{title}**"
    if source:
        head += f"  _{source}_"
    body = summary
    link = f"[查看原文]({url})" if url else ""
    parts = [head]
    if body:
        parts.append(body)
    if link:
        parts.append(link)
    return "\n".join(parts)


def build_card(digest: dict) -> dict:
    date = digest.get("date") or datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    sections = digest.get("sections") or {}
    meta = digest.get("meta") or {}

    elements: list[dict] = []
    first = True
    for key, label in SECTION_META:
        items = sections.get(key) or []
        if not items:
            continue
        if not first:
            elements.append({"tag": "hr"})
        first = False
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{label}**"},
        })
        for it in items:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": _fmt_item(it)},
            })

    if not elements:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "_今日无符合筛选条件的条目_"},
        })

    ok = meta.get("ok_sources", "?")
    total = meta.get("total_sources", "?")
    cst_now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%H:%M CST")
    note = f"数据源 {ok}/{total} 正常 · 生成于 {cst_now}"
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": note}],
    })

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📡 AI 日报 · {date}"},
                "template": "blue",
            },
            "elements": elements,
        },
    }


def build_text_message(text: str) -> dict:
    return {"msg_type": "text", "content": {"text": text}}
