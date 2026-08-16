"""Orchestrator — runs the full daily pipeline."""
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from .collectors.ministry_collector import MinistryCollector
from .collectors.press_collector import PressCollector
from .collectors.legislative_tracker import LegislativeTracker
from .collectors.research_monitor import ResearchMonitor
from .processors.deduplicator import Deduplicator
from .processors.importance_scorer import ImportanceScorer
from .processors.relevance_classifier import RelevanceClassifier
from .generators.report_compiler import ReportCompiler
from .generators.dashboard_builder import DashboardBuilder
from .search.history_search import HistorySearch
from .utils.logger import setup_logger
from .utils.whitelist import WhitelistManager

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(__file__).parent.parent
        setup_logger(self.base_dir / 'logs')

    # ── public ────────────────────────────────────────────────────────────────

    def run(self, date: Optional[str] = None) -> Path:
        today = date or datetime.now().strftime('%Y-%m-%d')
        logger.info(f"=== Policy Monitor: {today} ===")

        archive_dir = self.base_dir / 'archive' / today
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / 'raw').mkdir(exist_ok=True)

        wl = WhitelistManager(self.base_dir / 'sources')
        ministries = wl.load_ministries()
        press = wl.load_press()
        research = wl.load_research()

        all_items: list[dict] = []
        failed_sources: list[str] = []

        collectors = [
            ('ministry (정책브리핑)', MinistryCollector(ministries, self.base_dir)),
            ('press (언론사 RSS)',    PressCollector(press, self.base_dir, ministries)),
            ('legislation (국회)',   LegislativeTracker(ministries, self.base_dir)),
            ('research (연구기관)',   ResearchMonitor(research, self.base_dir)),
        ]

        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_name = {
                executor.submit(c.collect, today): name
                for name, c in collectors
            }
            for future in as_completed(future_to_name, timeout=180):
                name = future_to_name[future]
                try:
                    items = future.result()
                    logger.info(f"  [{name}] {len(items)}건 수집")
                    all_items.extend(items)
                except Exception as e:
                    logger.error(f"  [{name}] 실패: {e}", exc_info=True)
                    failed_sources.append(name)

        # 부처 ID → 한글 이름 변환 (ministry_name 미설정 항목 보정)
        self._resolve_ministry_names(all_items, ministries)

        # Save raw (AI 분류 전 원본)
        raw_path = archive_dir / 'raw' / 'collected.json'
        raw_path.write_text(
            json.dumps(all_items, ensure_ascii=False, indent=2, default=str),
            encoding='utf-8',
        )

        # AI 관련성 분류 (ANTHROPIC_API_KEY 있을 때만 동작)
        classifier = RelevanceClassifier(ministries)
        if classifier.is_available():
            logger.info("AI 관련성 분류 시작...")
            all_items = classifier.classify(all_items)
        else:
            logger.info("ANTHROPIC_API_KEY 미설정 → 키워드 필터 결과 사용")

        # Process
        deduped = Deduplicator().deduplicate(all_items)
        feedback_path = self.base_dir / 'feedback' / 'feedback.md'
        scored = ImportanceScorer(
            feedback_path if feedback_path.exists() else None
        ).score(deduped)

        # 항목 자체의 발행일(date)과, 이 파이프라인이 실제로 수집한 시각을 구분해서 남긴다.
        collected_at = datetime.now().strftime('%Y-%m-%d %H:%M')
        for item in scored:
            item.setdefault('collected_at', collected_at)

        # Generate outputs
        report_path = archive_dir / 'report.md'
        ReportCompiler().compile(scored, today, failed_sources, report_path)

        dashboard_path = archive_dir / 'dashboard.html'
        DashboardBuilder().build(scored, today, failed_sources, dashboard_path)

        # Update search index
        self._update_index(scored, today)

        logger.info(f"완료 - 대시보드: {dashboard_path}")
        if failed_sources:
            logger.warning(f"수집 실패 소스: {failed_sources}")

        return dashboard_path

    def search(
        self,
        query: str,
        ministry: Optional[str] = None,
        committee: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        searcher = HistorySearch(
            self.base_dir / 'index' / 'history-index.json',
            self.base_dir / 'archive',
        )
        return searcher.search(
            query,
            ministry=ministry,
            committee=committee,
            start_date=start_date,
            end_date=end_date,
        )

    # ── private ───────────────────────────────────────────────────────────────

    def _resolve_ministry_names(self, items: list[dict], ministries: dict) -> None:
        """부처 ID(예: 'motie')를 한글 이름(예: '산업통상자원부')으로 변환."""
        id_to_name: dict[str, str] = {}
        for committee in ministries.get('committees', []):
            for m in committee.get('ministries', []):
                id_to_name[m['id']] = m['name']

        for item in items:
            if not item.get('ministry_name'):
                mid = item.get('ministry', '')
                item['ministry_name'] = id_to_name.get(mid, mid) if mid else ''

    def _update_index(self, items: list[dict], date: str) -> None:
        index_path = self.base_dir / 'index' / 'history-index.json'
        index_path.parent.mkdir(exist_ok=True)

        if index_path.exists():
            existing = json.loads(index_path.read_text(encoding='utf-8'))
        else:
            existing = []

        # 항목은 이미 자기 발행일(date)을 갖고 있으므로 실행일로 덮어쓰지 않는다 —
        # research 항목은 최근 3개월 창을 매일 재수집하므로, 실행일로 덮으면 같은
        # 항목이 매번 다른 날짜로 찍혀 인덱스에 중복 누적된다.
        new_items = []
        for item in items:
            it = dict(item)
            it.setdefault('date', date)
            new_items.append(it)

        def _key(it: dict):
            return it.get('url') or (it.get('title', ''), it.get('date', ''), it.get('source', ''))

        merged: dict = {_key(e): e for e in existing}
        for it in new_items:
            merged[_key(it)] = it  # 재수집된 항목은 최신 내용(중요도/조회수 등)으로 갱신

        result = sorted(merged.values(), key=lambda e: e.get('date', ''), reverse=True)

        index_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding='utf-8',
        )
        logger.info(f"Index updated: {len(new_items)}건 처리 (dedupe 후 총 {len(result)}건)")
