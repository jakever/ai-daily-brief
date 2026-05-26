"""HTTP helpers — shared session with retries, UA, sane timeouts."""
from __future__ import annotations

import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def make_session(user_agent: str | None = None) -> requests.Session:
    sess = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    sess.headers.update({
        "User-Agent": user_agent or _DEFAULT_UA,
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    })
    return sess


def get(sess: requests.Session, url: str, *, timeout: int = 20, headers: dict | None = None) -> requests.Response:
    resp = sess.get(url, timeout=timeout, headers=headers or {})
    resp.raise_for_status()
    return resp


def post_json(sess: requests.Session, url: str, payload: dict, *, timeout: int = 20,
              max_attempts: int = 3, backoff: tuple[int, ...] = (0, 30, 60)) -> dict:
    """POST JSON with retry. Returns parsed JSON response."""
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        if attempt > 0:
            time.sleep(backoff[min(attempt, len(backoff) - 1)])
        try:
            r = sess.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_exc = e
    raise RuntimeError(f"POST {url} failed after {max_attempts} attempts: {last_exc}")
