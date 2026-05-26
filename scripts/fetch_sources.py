"""Fetch all sources concurrently, filter by 24h window, write raw.json.

Single-source failure is logged to errors[] and does not abort the run.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

# Allow running as a script: `python scripts/fetch_sources.py`
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.http import make_session
from lib.parsers import Item, parse_rss, parse_html, parse_hn_algolia


PARSERS = {
    "rss": parse_rss,
    "html": parse_html,
    "hn_algolia": parse_hn_algolia,
}


def filter_recent(items: list[Item], hours: int) -> list[Item]:
    """Keep items within last `hours`. Items with no timestamp pass through
    (most listing pages are time-ordered; we already cap with max_per_source)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: list[Item] = []
    for it in items:
        if not it.published_at:
            out.append(it)
            continue
        try:
            dt = datetime.fromisoformat(it.published_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                out.append(it)
        except Exception:
            out.append(it)
    return out


def fetch_one(src: dict, settings: dict) -> tuple[str, list[Item], str | None]:
    """Returns (source_name, items, error_or_none)."""
    name = src.get("name", "<unknown>")
    parser = PARSERS.get(src.get("type"))
    if parser is None:
        return name, [], f"{name}: unknown type {src.get('type')!r}"
    sess = make_session(user_agent=settings.get("user_agent"))
    try:
        items = parser(src, sess)
        items = filter_recent(items, settings.get("window_hours", 24))
        cap = settings.get("max_per_source", 15)
        items = items[:cap]
        return name, items, None
    except Exception as e:
        tb = traceback.format_exc(limit=3)
        return name, [], f"{name}: {type(e).__name__}: {e}\n{tb.strip().splitlines()[-1] if tb else ''}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sources.yaml")
    ap.add_argument("--output", default="raw.json")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        # allow running from scripts/ subdirectory
        cfg_path = Path(__file__).resolve().parent.parent / args.config
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    sources = cfg.get("sources", [])
    settings = cfg.get("settings", {})

    all_items: list[Item] = []
    errors: list[str] = []
    ok_sources = 0

    print(f"[fetch] {len(sources)} sources, window={settings.get('window_hours', 24)}h", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_one, src, settings): src for src in sources}
        for fut in as_completed(futures):
            name, items, err = fut.result()
            if err is not None:
                errors.append(err)
                print(f"  [FAIL] {err}", file=sys.stderr)
            else:
                ok_sources += 1
                all_items.extend(items)
                print(f"  [ OK ] {name}: {len(items)} items", file=sys.stderr)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok_sources": ok_sources,
        "total_sources": len(sources),
        "errors": errors,
        "items": [it.to_dict() for it in all_items],
    }
    Path(args.output).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[fetch] {len(all_items)} items, {ok_sources}/{len(sources)} ok, "
          f"{len(errors)} errors → {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
