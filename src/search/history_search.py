"""History search — queries the local archive index.

Supports:
  - keyword search (title + summary)
  - ministry filter
  - committee filter
  - date range filter
  - Falls back to scanning archive/*.md when index is missing/empty
"""
import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class HistorySearch:
    def __init__(self, index_path: Path, archive_dir: Path):
        self._index_path = index_path
        self._archive_dir = archive_dir

    # ── public ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        ministry: Optional[str] = None,
        committee: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        index = self._load_index()
        if not index:
            index = self._scan_archive()

        results = []
        q_lower = query.lower()
        for item in index:
            if not self._matches(item, q_lower, ministry, committee, start_date, end_date):
                continue
            results.append(item)

        # Sort: date desc, importance 상 first
        order = {'상': 0, '중': 1, '하': 2}
        results.sort(
            key=lambda x: (x.get('date', ''), order.get(x.get('importance', '하'), 9)),
            reverse=True,
        )
        return results

    # ── private ───────────────────────────────────────────────────────────────

    def _load_index(self) -> list[dict]:
        if not self._index_path.exists():
            return []
        try:
            with open(self._index_path, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Index load failed: {e}")
            return []

    def _scan_archive(self) -> list[dict]:
        """Fallback: scan archive/*/report.md files."""
        items = []
        for report_path in sorted(self._archive_dir.glob('*/report.md')):
            date = report_path.parent.name
            text = report_path.read_text(encoding='utf-8', errors='replace')
            # Extract items by pattern: `- [🔴🟡⚪] **[상|중|하]** [title](url)`
            for m in re.finditer(
                r'-\s+[🔴🟡⚪]\s+\*\*\[([상중하])\]\*\*\s+\[([^\]]+)\]\(([^)]+)\)',
                text
            ):
                items.append({
                    'importance': m.group(1),
                    'title': m.group(2),
                    'url': m.group(3),
                    'date': date,
                    'source': '',
                    'summary': '',
                    'committee': '',
                    'ministry': '',
                })
        return items

    @staticmethod
    def _matches(
        item: dict,
        q_lower: str,
        ministry: Optional[str],
        committee: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> bool:
        text = (
            (item.get('title') or '') + ' ' +
            (item.get('summary') or '') + ' ' +
            (item.get('source') or '') + ' ' +
            (item.get('ministry_name') or '')
        ).lower()

        if q_lower and q_lower not in text:
            return False

        if ministry:
            m_lower = ministry.lower()
            m_field = (item.get('ministry') or '') + (item.get('ministry_name') or '')
            if m_lower not in m_field.lower():
                return False

        if committee and committee not in (item.get('committee') or ''):
            return False

        item_date = item.get('date', '')
        if start_date and item_date and item_date < start_date:
            return False
        if end_date and item_date and item_date > end_date:
            return False

        return True
