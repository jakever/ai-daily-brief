"""Source parsers: RSS / HTML (config-driven) / HN Algolia."""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
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


# --- RSS ---

def parse_rss(src: dict, sess) -> list[Item]:
    # feedparser handles fetch + parse, but routing through our session lets us reuse UA.
    resp = get(sess, src["url"], timeout=20)
    feed = feedparser.parse(resp.content)
    out: list[Item] = []
    for entry in feed.entries:
        published = (
            _to_iso(getattr(entry, "published_parsed", None))
            or _to_iso(getattr(entry, "updated_parsed", None))
            or _to_iso(getattr(entry, "published", ""))
        )
        out.append(Item(
            source=src["name"],
            title=_strip_html(getattr(entry, "title", "")).strip(),
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

        out.append(Item(
            source=src["name"],
            title=title.strip()[:240],
            url=link,
            summary=summary,
            published_at=published,
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
