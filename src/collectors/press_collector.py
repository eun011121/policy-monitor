"""Press collector — collects news articles from whitelisted RSS feeds.

1단계 필터:
  - ANTHROPIC_API_KEY 있을 때: `keywords` (넓은 단어 수준) 로 후보 추출
  - API 키 없을 때: `strict_keywords` (구체적 복합어) 로 엄격하게 필터
2단계: RelevanceClassifier (orchestrator에서 호출) 가 AI로 최종 판단
"""
import html as html_module
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

import feedparser
from bs4 import BeautifulSoup

from .base import BaseCollector

logger = logging.getLogger(__name__)

# 부처 보도자료(ministry_collector)와 동일하게 최근 1주일 범위로 뉴스를 수집한다
# (CLAUDE.md 규칙 10) — 하루치만 보면 며칠 전 터진 대형 이슈(세제개편안 등)를 놓친다.
NEWS_LOOKBACK_DAYS = 6

_CAPTION_RE = re.compile(
    r'[^\n.!?]*(?:연합뉴스|사진기자단|이미지 제공|제공|사진은 기사와 무관)[^\n.!?]*[.\s]*'
)


def _clean_summary(text: str, max_len: int = 180) -> str:
    text = html_module.unescape(text)
    text = _CAPTION_RE.sub(' ', text)
    text = re.sub(r'\.([가-힣A-Z])', r'. \1', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    if len(text) > max_len:
        cut = text[:max_len].rsplit(' ', 1)[0]
        text = cut.rstrip('.,') + '…'
    return text


class PressCollector(BaseCollector):
    item_type = 'news'

    def __init__(self, press: dict, base_dir, ministries: dict):
        super().__init__()
        self._press = press
        self._base_dir = base_dir
        # AI 분류기 사용 여부에 따라 키워드 세트 선택
        use_wide = bool(os.environ.get('ANTHROPIC_API_KEY', ''))
        self._global_excludes: list[str] = ministries.get('global_exclude', [])
        self._keywords = self._build_keywords(ministries, use_wide=use_wide)
        mode = "넓은(AI 2단계)" if use_wide else "엄격한(키워드만)"
        logger.info(f"PressCollector: {mode} 키워드 세트 사용")

    # ── public ────────────────────────────────────────────────────────────────

    def collect(self, date: str) -> list[dict]:
        items: list[dict] = []
        since = datetime.strptime(date, '%Y-%m-%d') - timedelta(days=NEWS_LOOKBACK_DAYS)

        for paper in self._press.get('newspapers', []):
            for feed_info in paper.get('rss_feeds', []):
                try:
                    new_items = self._collect_rss(paper, feed_info, since, date)
                    items.extend(new_items)
                except Exception as e:
                    logger.error(
                        f"Press RSS failed [{paper['name']} {feed_info.get('url')}]: {e}"
                    )

        logger.info(f"PressCollector: {len(items)} items for {date}")
        return items

    # ── private ───────────────────────────────────────────────────────────────

    def _collect_rss(
        self, paper: dict, feed_info: dict, since: datetime, date: str
    ) -> list[dict]:
        items = []
        feed = feedparser.parse(feed_info['url'])
        if feed.bozo:
            logger.debug(f"RSS parse warning [{paper['name']}]: {feed.bozo_exception}")

        for entry in feed.entries:
            pub = self._parse_entry_date(entry)
            if pub and pub < since:
                continue
            pub_str = pub.strftime('%Y-%m-%d') if pub else date

            title = entry.get('title', '').strip()
            raw_summary = entry.get('summary', '') or entry.get('description', '')
            plain = BeautifulSoup(raw_summary, 'lxml').get_text(separator=' ')
            summary = _clean_summary(plain)
            url = entry.get('link', '').strip()

            text = title + ' ' + summary

            # strict 모드에서만 global_exclude 적용 (AI 모드에서는 Claude가 판단)
            if self._global_excludes and self._is_globally_excluded(text):
                continue

            committee_id, ministry_id = self._classify(text)
            if not committee_id:
                continue

            items.append(self._fmt_item(
                title=title,
                url=url,
                date=pub_str,
                source=paper['name'],
                summary=summary,
                committee=committee_id,
                ministry=ministry_id or '',
                item_type=self.item_type,
            ))
        return items

    def _is_globally_excluded(self, text: str) -> bool:
        for kw in self._global_excludes:
            if kw in text:
                return True
        return False

    def _build_keywords(
        self, ministries: dict, use_wide: bool
    ) -> list[tuple[list[str], list[str], str, Optional[str]]]:
        """Returns (keywords, exclude_keywords, committee_id, ministry_id_or_None)."""
        mapping = []
        kw_field = 'keywords' if use_wide else 'strict_keywords'
        for committee in ministries.get('committees', []):
            c_excl = committee.get('exclude_keywords', [])
            mapping.append((
                [committee.get('short', ''), committee.get('name', '')],
                c_excl,
                committee['id'],
                None,
            ))
            for ministry in committee.get('ministries', []):
                # wide 모드: 넓은 keywords, strict 모드: strict_keywords (없으면 keywords 폴백)
                keywords = ministry.get(kw_field) or ministry.get('keywords', [ministry['name']])
                excl = ministry.get('exclude_keywords', [])
                mapping.append((keywords, excl, committee['id'], ministry['id']))
        return mapping

    def _classify(self, text: str) -> tuple[Optional[str], Optional[str]]:
        for keywords, excl_kws, committee_id, ministry_id in self._keywords:
            for kw in keywords:
                if not kw:
                    continue
                if kw in text:
                    if any(ex in text for ex in excl_kws):
                        break
                    return committee_id, ministry_id
        return None, None

    @staticmethod
    def _parse_entry_date(entry) -> Optional[datetime]:
        for field in ('published_parsed', 'updated_parsed'):
            val = entry.get(field)
            if val:
                try:
                    return datetime(*val[:6])
                except Exception:
                    pass
        return None
