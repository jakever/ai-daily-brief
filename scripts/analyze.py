"""Call Claude Sonnet 4.6 to filter / translate / classify / summarize raw items."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic


MODEL = "claude-sonnet-4-6"
SECTIONS = ["frontier", "breaking", "oversea", "cn", "trending"]
FIELD_SEP = "|||"


def _today_cst_date() -> str:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def _parse_digest(text: str) -> dict:
    """Parse the delimited plain-text digest format the model emits.

    Line-based, no JSON — sidesteps the relay's unreliable nested-object/quote
    escaping. Format:
        DATE: YYYY-MM-DD
        [section]
        title ||| summary ||| url ||| source
    """
    text = text.strip()
    # Tolerate accidental ``` fences around the block.
    text = re.sub(r"^```[a-z]*\n?|```$", "", text.strip()).strip()

    date = ""
    sections: dict[str, list] = {s: [] for s in SECTIONS}
    current: str | None = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("DATE:"):
            date = line.split(":", 1)[1].strip()
            continue
        m = re.match(r"^\[(\w+)\]$", line)
        if m:
            current = m.group(1).lower()
            continue
        if current in sections and FIELD_SEP in line:
            parts = [p.strip() for p in line.split(FIELD_SEP)]
            # Pad/truncate to exactly 4 fields.
            parts = (parts + ["", "", "", ""])[:4]
            title, summary, url, source = parts
            if title:
                sections[current].append(
                    {"title": title, "summary": summary, "url": url, "source": source}
                )
    if not any(sections.values()):
        raise ValueError("No items parsed from model response")
    return {"date": date or _today_cst_date(), "sections": sections}


def _fallback_digest(raw: dict, reason: str) -> dict:
    """If LLM call fails, ship a degraded digest with raw titles grouped by section_hint."""
    buckets: dict[str, list] = {s: [] for s in SECTIONS}
    for it in raw.get("items", [])[:20]:
        hint = it.get("section_hint") or "cn"
        if hint not in buckets:
            hint = "cn"
        buckets[hint].append({
            "title": it.get("title", ""),
            "summary": (it.get("summary", "") or "")[:120],
            "url": it.get("url", ""),
            "source": it.get("source", ""),
        })
    return {
        "date": _today_cst_date(),
        "sections": buckets,
        "_degraded": reason,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="raw.json")
    ap.add_argument("--output", default="digest.json")
    ap.add_argument("--prompt", default="prompts/analyze.md")
    args = ap.parse_args()

    raw_path = Path(args.input)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))

    items = raw.get("items", [])
    if not items:
        print("[analyze] no items, writing empty digest", file=sys.stderr)
        digest = {"date": _today_cst_date(), "sections": {s: [] for s in SECTIONS}}
        digest["meta"] = {
            "ok_sources": raw.get("ok_sources", 0),
            "total_sources": raw.get("total_sources", 0),
            "errors": raw.get("errors", []),
        }
        Path(args.output).write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    # Compact items for token economy
    compact = [
        {
            "source": it.get("source", ""),
            "title": (it.get("title", "") or "").strip()[:200],
            "url": it.get("url", ""),
            "summary": (it.get("summary", "") or "").strip()[:200],
            "section_hint": it.get("section_hint", ""),
        }
        for it in items
    ]
    print(f"[analyze] {len(compact)} items → {MODEL}", file=sys.stderr)

    prompt_path = Path(args.prompt)
    if not prompt_path.exists():
        prompt_path = Path(__file__).resolve().parent.parent / args.prompt
    system_prompt = prompt_path.read_text(encoding="utf-8")

    user_content = (
        f"今日日期: {_today_cst_date()}\n"
        f"今日条目（共 {len(compact)} 条）:\n"
        f"{json.dumps(compact, ensure_ascii=False)}"
    )

    client_kwargs = {"api_key": os.environ.get("ANTHROPIC_API_KEY")}
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url
        print(f"[analyze] using base_url={base_url}", file=sys.stderr)
    client = anthropic.Anthropic(**client_kwargs)

    digest: dict | None = None
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=12000,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_content}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            digest = _parse_digest(text)
            break
        except Exception as e:
            last_err = e
            print(f"[analyze] attempt {attempt + 1} failed: {e}", file=sys.stderr)

    if digest is None:
        print(f"[analyze] all attempts failed, using degraded fallback", file=sys.stderr)
        digest = _fallback_digest(raw, str(last_err))

    digest.setdefault("date", _today_cst_date())
    digest.setdefault("sections", {s: [] for s in SECTIONS})
    digest["meta"] = {
        "ok_sources": raw.get("ok_sources", 0),
        "total_sources": raw.get("total_sources", 0),
        "errors": raw.get("errors", []),
    }

    Path(args.output).write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {k: len(v) for k, v in digest["sections"].items()}
    print(f"[analyze] sections: {counts} → {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
