"""Filter / translate / classify / summarize raw items with an LLM.

Talks to the relay over the OpenAI-compatible `/v1/chat/completions` endpoint via
plain HTTP + SSE streaming, rather than through a vendor SDK. Two reasons, both
found the hard way:

  * Protocol reach — the relay only exposes Anthropic's `/v1/messages` for paid
    models. Free variants (kimi-k3-free, glm-*-free) are chat/completions-only,
    so an Anthropic SDK simply cannot address them.
  * The 120s wall — the relay sits behind Cloudflare, which aborts any request
    whose origin stays silent for 120s (error 524). Free-tier models need 4-5
    minutes for this job. Streaming keeps bytes flowing, so the gateway never
    fires; a non-streamed call is guaranteed to fail.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests


# Overridable so the model can be swapped without touching CI: `ANALYZE_MODEL=...`.
# moonshotai/kimi-k3-free is NOT a valid choice here: it rejects every streamed
# request with `openai_error` (works non-streamed, but streaming is mandatory —
# see the 120s note above). glm-5.3-free / deepseek-v4-pro-free also stream fine
# but are slower.
MODEL = os.environ.get("ANALYZE_MODEL", "qwen/qwen3.8-max-free")
SECTIONS = ["frontier", "breaking", "oversea", "cn", "trending"]
FIELD_SEP = "|||"
MAX_TOKENS = 12000
TOP_P = 0.95
# Degraded-mode cap per section, matching lark_card.VISIBLE so a fallback
# digest fills the card's inline slots without spilling into the fold.
FALLBACK_PER_SECTION = 5
# Per-attempt read budget. Kept well under the CI job timeout so that two
# attempts plus fetch/send still fit inside it.
READ_TIMEOUT = 600


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
    """If LLM call fails, ship a degraded digest with raw titles grouped by section_hint.

    Capped *per section*, not globally: raw.json is ordered by whichever source's
    concurrent fetch finished first, so an earlier global items[:20] slice could
    drop whole sections — frontier and trending came back empty whenever the
    Chinese media feeds (which carry the most items) happened to land first.
    """
    buckets: dict[str, list] = {s: [] for s in SECTIONS}
    for it in raw.get("items", []):
        hint = it.get("section_hint") or "cn"
        if hint not in buckets:
            hint = "cn"
        if len(buckets[hint]) >= FALLBACK_PER_SECTION:
            continue
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


def _stream_chat(base_url: str, api_key: str, system_prompt: str, user_content: str) -> str:
    """POST one streamed chat completion and return the assistant's text.

    Reasoning models emit their chain of thought in `delta.reasoning_content`,
    which is 3-4x the size of the answer here and must not reach the parser —
    only `delta.content` is collected.
    """
    resp = requests.post(
        base_url.rstrip("/") + "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "accept": "text/event-stream",
        },
        json={
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "stream": True,
            # Sent explicitly on purpose: left absent, the relay injects
            # top_p=0.001, which some upstreams reject outright. It also
            # rewrites 1 -> 0.999 (likewise rejected), so pick a value it
            # passes through untouched.
            "top_p": TOP_P,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        },
        stream=True,
        timeout=(20, READ_TIMEOUT),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    chunks: list[str] = []
    # Decode the SSE bytes ourselves: requests' decode_unicode=True falls back to
    # the HTTP default of ISO-8859-1 when the response carries no charset, which
    # mangles every CJK character in the digest.
    for raw in resp.iter_lines(decode_unicode=False):
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace")
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue        # keep-alive or partial frame
        delta = ((event.get("choices") or [{}])[0]).get("delta") or {}
        piece = delta.get("content")
        if piece:
            chunks.append(piece)
    return "".join(chunks)


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

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if not base_url:
        # The chat/completions shape is the relay's, not the vendor's official API.
        print("[analyze] ANTHROPIC_BASE_URL is required (relay endpoint)", file=sys.stderr)
        return 2
    print(f"[analyze] using base_url={base_url}", file=sys.stderr)

    digest: dict | None = None
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            text = _stream_chat(base_url, api_key, system_prompt, user_content)
            print(f"[analyze] attempt {attempt + 1}: {len(text)} chars streamed", file=sys.stderr)
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
