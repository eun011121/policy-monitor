"""Base collector with shared HTTP / parsing helpers."""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

REQUEST_DELAY = 1.5  # seconds between requests
TIMEOUT = 20
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


class BaseCollector:
    item_type: str = 'base'

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self._last_request: float = 0.0

    def collect(self, date: str) -> list[dict]:
        raise NotImplementedError

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self, url: str, **kwargs) -> Optional[requests.Response]:
        elapsed = time.time() - self._last_request
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        try:
            resp = self._session.get(url, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            self._last_request = time.time()
            return resp
        except Exception as e:
            logger.warning(f"GET {url} failed: {e}")
            return None

    def _post(self, url: str, **kwargs) -> Optional[requests.Response]:
        elapsed = time.time() - self._last_request
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        try:
            resp = self._session.post(url, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            self._last_request = time.time()
            return resp
        except Exception as e:
            logger.warning(f"POST {url} failed: {e}")
            return None

    def _soup(self, url: str) -> Optional[BeautifulSoup]:
        resp = self._get(url)
        if resp is None:
            return None
        resp.encoding = resp.apparent_encoding or 'utf-8'
        return BeautifulSoup(resp.text, 'lxml')

    # ── date helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _since(date: str) -> datetime:
        """Return datetime for the day before `date` (cutoff for 'new' items)."""
        return datetime.strptime(date, '%Y-%m-%d') - timedelta(days=1)

    @staticmethod
    def _fmt_item(
        title: str,
        url: str,
        date: str,
        source: str,
        summary: str,
        committee: str,
        ministry: str,
        item_type: str,
        extra: Optional[dict] = None,
    ) -> dict:
        item = {
            'title': title.strip(),
            'url': url.strip(),
            'date': date,
            'source': source,
            'summary': summary.strip() if summary else '',
            'committee': committee,
            'ministry': ministry,
            'type': item_type,
            'importance': None,
            'cluster_id': None,
            'related': [],
        }
        if extra:
            item.update(extra)
        return item
