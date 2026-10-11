"""Shared HTTP session with retries on transient network errors."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "cloudbuster-sync"


def session(timeout: float = 60.0) -> requests.Session:
    retry = Retry(
        total=5,
        connect=5,
        read=3,
        backoff_factor=2,
        status_forcelist=(502, 503, 504),
        # Only methods that are safe to repeat. Uploads (POST) are not retried
        # here; a failed run is simply retried on the next poll.
        allowed_methods=frozenset({"GET", "HEAD", "PROPFIND", "PUT", "DELETE", "MOVE"}),
        raise_on_status=False,
    )
    s = requests.Session()
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = USER_AGENT
    s.request = _with_timeout(s.request, timeout)  # type: ignore[method-assign]
    return s


def _with_timeout(request, timeout):
    def wrapped(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return request(method, url, **kwargs)
    return wrapped
