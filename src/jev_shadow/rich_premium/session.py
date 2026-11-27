"""Rate-limited HTTP session for the public NSE and BSE archives."""

from __future__ import annotations

import time
from typing import Any

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
MIN_GAP_S = 1.0
ATTEMPTS = 4


class Polite:
    def __init__(self, warm_home: bool = False) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self.last = 0.0
        self.warm_home = warm_home
        if warm_home:
            self.warm()

    def warm(self) -> None:
        self.get("https://www.nseindia.com/")

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        response: requests.Response | None = None
        for attempt in range(ATTEMPTS):
            wait = MIN_GAP_S - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                response = self.session.get(url, timeout=40, **kwargs)
            except requests.RequestException as error:
                print("neterr", url, error, flush=True)
                time.sleep(5 * (attempt + 1))
                continue
            if response.status_code in (401, 403) and self.warm_home and "nseindia.com/api" in url:
                time.sleep(3)
                self.warm()
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(10 * (attempt + 1))
                continue
            return response
        if response is None:
            raise requests.ConnectionError(f"no response from {url} after {ATTEMPTS} attempts")
        return response
