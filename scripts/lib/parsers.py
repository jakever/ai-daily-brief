"""Source parsers: RSS / HTML (config-driven) / HN Algolia."""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dateparse

from .http import get


@dataclass
class Item:
    source: str
    title: str
    url: str
    summary: str
    published_at: str  # ISO 8601 UTC string; "" if unknown
    section_hint: str

    def to_dict(self) -> dict:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_iso(value) -> str:
    if not value:
        return ""
    try:
        if isinstance(value, str):
            dt = dateparse.parse(value)
        elif isinstance(value, (tuple, list)) and len(value) >= 6:
            dt = datetime(*value[:6], tzinfo=timezone.utc)
        else:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return ""


def _strip_html(s: str) -> str:
    if not s:
        return ""
    text = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", text).strip()


# Matches dates embedded in listing text, e.g. "May 28, 2026" / "Jun 2, 2026".
_DATE_IN_TEXT = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}\b"
)


def _date_from_text(text: str) -> str:
    """Best-effort: pull a publish date out of listing text when there's no
    dedicated time element (e.g. Anthropic news cards embed 'May 28, 2026')."""
    if not text:
        return ""
    m = _DATE_IN_TEXT.search(text)
    return _to_iso(m.group(0)) if m else ""


# --- RSS ---

def parse_rss(src: dict, sess) -> list[Item]:
    # feedparser handles fetch + parse, but routing through our session lets us reuse UA.
    resp = get(sess, src["url"], timeout=20)
    feed = feedparser.parse(resp.content)
    # Some feeds carry no context in the title (GitHub releases.atom titles are bare
    # version strings like "v0.1.1-rc.2"); `title_prefix` restores it.
    prefix = src.get("title_prefix", "")
    out: list[Item] = []
    for entry in feed.entries:
        published = (
            _to_iso(getattr(entry, "published_parsed", None))
            or _to_iso(getattr(entry, "updated_parsed", None))
            or _to_iso(getattr(entry, "published", ""))
        )
        title = _strip_html(getattr(entry, "title", "")).strip()
        out.append(Item(
            source=src["name"],
            title=f"{prefix}{title}" if prefix and title else title,
            url=getattr(entry, "link", "").strip(),
            summary=_strip_html(getattr(entry, "summary", ""))[:400],
            published_at=published,
            section_hint=src.get("section_hint", ""),
        ))
    return out


# --- HTML ---

def _extract(node, selector_spec: str) -> str:
    """selector_spec supports 'css' or 'css@attr' and comma-separated fallbacks."""
    if not selector_spec:
        return ""
    for spec in [s.strip() for s in selector_spec.split(",")]:
        if not spec:
            continue
        if "@" in spec:
            sel, attr = spec.rsplit("@", 1)
            sel = sel.strip()
            target = node if not sel else (node.select_one(sel) if sel else node)
            if target is None:
                continue
            val = target.get(attr, "")
            if val:
                return val.strip()
        else:
            target = node.select_one(spec)
            if target is not None:
                txt = target.get_text(" ", strip=True)
                if txt:
                    return txt
    return ""


def parse_html(src: dict, sess) -> list[Item]:
    headers = src.get("headers") or {}
    resp = get(sess, src["url"], timeout=20, headers=headers)
    soup = BeautifulSoup(resp.text, "html.parser")
    sel = src.get("selector") or {}
    base_url = src.get("base_url", src["url"])

    item_sel = sel.get("item")
    if not item_sel:
        return []

    nodes = []
    for spec in [s.strip() for s in item_sel.split(",")]:
        nodes = soup.select(spec)
        if nodes:
            break
    if not nodes:
        return []

    out: list[Item] = []
    seen_urls: set[str] = set()
    for node in nodes:
        title = _extract(node, sel.get("title", "")) or node.get_text(" ", strip=True)[:120]
        link = _extract(node, sel.get("link", "")) or (node.get("href") if hasattr(node, "get") else "")
        if not link and hasattr(node, "select_one"):
            a = node.select_one("a")
            if a is not None:
                link = a.get("href", "")
        if link:
            link = urljoin(base_url, link)
        if not title or not link:
            continue
        if link in seen_urls:
            continue
        seen_urls.add(link)

        summary = _extract(node, sel.get("summary", ""))[:400]
        published = _to_iso(_extract(node, sel.get("time", "")))
        if not published:
            # Fallback: many listing pages embed the date in the card text.
            published = _date_from_text(node.get_text(" ", strip=True))

        out.append(Item(
            source=src["name"],
            title=title.strip()[:240],
            url=link,
            summary=summary,
            published_at=published,
            section_hint=src.get("section_hint", ""),
        ))
    return out


# --- Markdown changelog (e.g. Claude Code CHANGELOG.md) ---

def parse_changelog_md(src: dict, sess) -> list[Item]:
    """Parse a `## version` + bullet-list markdown changelog. Emits the latest
    N version blocks as items. No per-version dates → pair with assume_fresh."""
    resp = get(sess, src["url"], timeout=20)
    text = resp.text
    max_versions = int(src.get("max_versions", 2))
    link = src.get("link_url", src["url"])
    # Split into (version, body) blocks on level-2 headings.
    blocks = re.split(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE)
    # blocks = [pre, ver1, body1, ver2, body2, ...]
    out: list[Item] = []
    for i in range(1, len(blocks) - 1, 2):
        version = blocks[i].strip()
        body = blocks[i + 1]
        bullets = [b.strip("-* ").strip() for b in body.splitlines() if b.strip().startswith(("-", "*"))]
        if not bullets:
            continue
        summary = "；".join(bullets[:4])
        out.append(Item(
            source=src["name"],
            title=f"{src['name']} {version}",
            url=link,
            summary=_strip_html(summary)[:400],
            published_at="",
            section_hint=src.get("section_hint", ""),
        ))
        if len(out) >= max_versions:
            break
    return out


# --- ai-bot.cn 每日 AI 快讯 ---

def parse_ai_bot_daily(src: dict, sess) -> list[Item]:
    """ai-bot.cn/daily-ai-news 列表页顶部即为当日逐条快讯：`.content` 内 `div.news-date`
    («6月3·周三») 标记日期，其后跟随多个 `div.news-item`（h2 标题 + p 描述 + a.external
    外链）。抓「今日(Asia/Shanghai)」日期段下的条目；当天段不存在则退回最新一天。"""
    now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
    resp = get(sess, src["url"], timeout=20)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    content = soup.select_one(".content") or soup

    # `.news-date` and `.news-item` in document order; slice items between the
    # target date and the next date marker.
    blocks = content.select(".news-date, .news-item")
    today_label = f"{now.month}月{now.day}·"          # e.g. "6月3·" — trailing dot avoids 6月3↔6月30 clash
    start = None
    for i, b in enumerate(blocks):
        cls = b.get("class") or []
        if "news-date" in cls and b.get_text(strip=True).startswith(today_label):
            start = i
            break
    if start is None:
        # No section for today yet — fall back to the first (latest) date block.
        start = next((i for i, b in enumerate(blocks) if "news-date" in (b.get("class") or [])), None)
        if start is None:
            return []

    out: list[Item] = []
    for b in blocks[start + 1:]:
        cls = b.get("class") or []
        if "news-date" in cls:
            break       # reached the next day
        if "news-item" not in cls:
            continue
        h = b.select_one("h2, h3, .news-title")
        title = h.get_text(strip=True) if h else ""
        p = b.select_one("p")
        summary = p.get_text(" ", strip=True) if p else ""
        a = b.select_one("a.external[href], a[href^='http']")
        url = a.get("href") if a else src["url"]
        if not title:
            continue
        out.append(Item(
            source=src["name"],
            title=title[:120],
            url=url,
            summary=summary[:300],
            published_at=now.isoformat(),
            section_hint=src.get("section_hint", ""),
        ))
    return out


# --- HN Algolia ---

def parse_hn_algolia(src: dict, sess) -> list[Item]:
    resp = get(sess, src["url"], timeout=20)
    data = resp.json()
    out: list[Item] = []
    for hit in data.get("hits", []):
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        ts = hit.get("created_at") or ""
        out.append(Item(
            source=src["name"],
            title=(hit.get("title") or hit.get("story_title") or "").strip(),
            url=url,
            summary=(hit.get("story_text") or "")[:300],
            published_at=_to_iso(ts),
            section_hint=src.get("section_hint", ""),
        ))
    return [it for it in out if it.title and it.url]
