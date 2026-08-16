"""Whitelist manager — loads sources/*.yaml and provides domain validation."""
import logging
from pathlib import Path
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)


class WhitelistManager:
    def __init__(self, sources_dir: Path):
        self.sources_dir = sources_dir
        self._allowed_domains: set[str] = set()

    # ── loaders ──────────────────────────────────────────────────────────────

    def load_ministries(self) -> dict:
        return self._load('ministries.yaml')

    def load_press(self) -> dict:
        return self._load('press-whitelist.yaml')

    def load_research(self) -> dict:
        return self._load('research-institutes.yaml')

    def _load(self, filename: str) -> dict:
        path = self.sources_dir / filename
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f)
        self._register_domains(data)
        return data

    # ── domain registry ───────────────────────────────────────────────────────

    def _register_domains(self, data) -> None:
        if isinstance(data, dict):
            for v in data.values():
                self._register_domains(v)
        elif isinstance(data, list):
            for item in data:
                self._register_domains(item)
        elif isinstance(data, str) and data.startswith('http'):
            domain = urlparse(data).netloc
            if domain:
                self._allowed_domains.add(domain)

    def is_allowed(self, url: str) -> bool:
        if not url:
            return False
        domain = urlparse(url).netloc
        # Allow exact match or subdomain match
        for allowed in self._allowed_domains:
            if domain == allowed or domain.endswith('.' + allowed):
                return True
        return False

    def assert_allowed(self, url: str) -> None:
        if not self.is_allowed(url):
            raise ValueError(f"URL not in whitelist: {url}")
