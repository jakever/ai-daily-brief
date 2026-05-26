"""Send Lark interactive card from digest.json."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.http import make_session, post_json
from lib.lark_card import build_card, build_text_message


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="digest.json")
    ap.add_argument("--text", help="Send a plain text message instead of card (for testing)")
    args = ap.parse_args()

    webhook = os.environ.get("LARK_WEBHOOK_URL")
    if not webhook:
        print("[send] LARK_WEBHOOK_URL not set", file=sys.stderr)
        return 2

    sess = make_session()

    if args.text:
        payload = build_text_message(args.text)
    else:
        digest = json.loads(Path(args.input).read_text(encoding="utf-8"))
        payload = build_card(digest)

    try:
        resp = post_json(sess, webhook, payload, timeout=20, max_attempts=3, backoff=(0, 30, 60))
    except Exception as e:
        print(f"[send] webhook POST failed: {e}", file=sys.stderr)
        return 1

    code = resp.get("code", resp.get("StatusCode", -1))
    if code != 0:
        print(f"[send] Lark rejected: {resp}", file=sys.stderr)
        return 1

    print(f"[send] OK → {resp}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
