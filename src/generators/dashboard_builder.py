"""dashboard_builder.py — generates dashboard.html from scored items.

Self-contained HTML: no external CDN, works offline (로컬 더블클릭 실행).
Features:
  - 탭 기반 위원회별 섹션
  - 중요도 뱃지 (상/중/하)
  - 검색창 (title/summary 필터)
  - 수집 실패 경고 배너
  - 반응형 레이아웃
"""
import json
import logging
from collections import defaultdict
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

COMMITTEE_NAMES = {
    'industry':    '산자중기위',
    'science':     '과방위',
    'agriculture': '농해수위',
    'politics':    '정무위',
    'common':      '공통 이슈',
}

RESEARCH_TAB_LABEL = '연구기관 (최근 3개월)'

# sources/ministries.yaml에 등록된 순서 그대로 — report_compiler.py의 동일 상수와 맞춤
MINISTRY_ORDER = {
    'industry':    ['산업통상부', '중소벤처기업부', '지식재산처'],
    'science':     ['과학기술정보통신부', '방송미디어통신위원회', '원자력안전위원회', '우주항공청'],
    'agriculture': ['농림축산식품부', '해양수산부', '농촌진흥청', '산림청', '해양경찰청'],
    'politics':    ['금융위원회', '공정거래위원회', '국무조정실·국무총리비서실', '국민권익위원회'],
}


def _ordered_keys(by_group: dict, order: list):
    ordered = [name for name in order if name in by_group]
    leftover = [name for name in by_group if name not in order]
    return ordered + leftover

IMPORTANCE_CLASS = {'상': 'high', '중': 'mid', '하': 'low'}
IMPORTANCE_LABEL = {'상': '상', '중': '중', '하': '하'}


class DashboardBuilder:
    def build(
        self,
        items: list[dict],
        date: str,
        failed_sources: list[str],
        output_path: Path,
    ) -> None:
        # Prepare data for template
        top5 = [i for i in items if i.get('importance') == '상'][:5]

        committee_sections: dict[str, list[dict]] = {k: [] for k in COMMITTEE_NAMES}
        legislation_items: list[dict] = []
        research_items: list[dict] = []

        for item in items:
            if item['type'] == 'legislation':
                legislation_items.append(item)
            elif item['type'] == 'research':
                # 소관 상임위와 무관하게 전부 별도 "연구기관" 탭으로 (부처 탭과 섞지 않음)
                research_items.append(item)
            else:
                c = item.get('committee', 'common')
                if c in committee_sections:
                    committee_sections[c].append(item)
                else:
                    committee_sections['common'].append(item)

        research_items.sort(key=lambda i: i.get('date', ''), reverse=True)

        html = self._render(
            date, failed_sources, top5, committee_sections, legislation_items, research_items, items
        )
        output_path.write_text(html, encoding='utf-8')
        logger.info(f"Dashboard written: {output_path}")

    # ── renderer ──────────────────────────────────────────────────────────────

    def _render(
        self,
        date: str,
        failed_sources: list[str],
        top5: list[dict],
        sections: dict[str, list[dict]],
        legislation: list[dict],
        research: list[dict],
        all_items: list[dict],
    ) -> str:
        failed_html = ''
        if failed_sources:
            items_html = ''.join(f'<li>{s}</li>' for s in failed_sources)
            failed_html = f'<div class="alert"><strong>⚠️ 오늘 수집 실패:</strong><ul>{items_html}</ul></div>'

        top5_html = ''
        for idx, item in enumerate(top5, 1):
            top5_html += f'''
            <div class="headline-item">
              <span class="headline-num">{idx}</span>
              <a href="{item["url"]}" target="_blank">{item["title"]}</a>
              <span class="source-tag">{item["source"]} · {item.get("date", "")}</span>
            </div>'''

        # Tabs
        tab_ids = list(COMMITTEE_NAMES.keys()) + ['research', 'legislation']
        tab_buttons = ''
        tab_contents = ''

        for tab_id in tab_ids:
            if tab_id == 'legislation':
                label = '입법 현황'
                count = len(legislation)
                content = self._render_legislation(legislation)
            elif tab_id == 'research':
                label = RESEARCH_TAB_LABEL
                count = len(research)
                content = self._render_research(research)
            else:
                label = COMMITTEE_NAMES[tab_id]
                tab_items = sections.get(tab_id, [])
                count = len(tab_items)
                content = self._render_items(
                    tab_items,
                    group_by=None if tab_id == 'common' else 'ministry',
                    committee_id=tab_id,
                )

            active = 'active' if tab_id == 'industry' else ''
            tab_buttons += f'<button class="tab-btn {active}" onclick="switchTab(\'{tab_id}\')" id="btn-{tab_id}">{label} <span class="count">{count}</span></button>'
            display = 'block' if tab_id == 'industry' else 'none'
            tab_contents += f'<div id="tab-{tab_id}" class="tab-content" style="display:{display}">{content}</div>'

        # All items as JSON for client-side search
        items_json = json.dumps(all_items, ensure_ascii=False, default=str)

        return f'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>정책 모니터링 대시보드 — {date}</title>
<style>
  :root {{
    --bg: #f8f9fa; --card: #fff; --border: #dee2e6;
    --text: #212529; --muted: #6c757d;
    --high: #dc3545; --mid: #ffc107; --low: #6c757d;
    --accent: #0d6efd; --tab-active: #0d6efd; --tab-bg: #e9ecef;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #1a1a2e; --card: #16213e; --border: #374151;
      --text: #e2e8f0; --muted: #9ca3af;
      --high: #f87171; --mid: #fbbf24; --low: #9ca3af;
      --accent: #60a5fa; --tab-active: #60a5fa; --tab-bg: #0f3460;
    }}
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, sans-serif;
          background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.6; }}
  header {{ background: var(--accent); color: #fff; padding: 16px 24px;
            display: flex; justify-content: space-between; align-items: center; }}
  header h1 {{ font-size: 18px; font-weight: 700; }}
  header .meta {{ font-size: 12px; opacity: .85; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 16px; }}
  .alert {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 12px 16px;
            margin-bottom: 16px; border-radius: 4px; font-size: 13px; color: #856404; }}
  .alert ul {{ margin-left: 20px; }}
  /* search */
  .search-bar {{ display: flex; gap: 8px; margin-bottom: 16px; }}
  .search-bar input {{ flex: 1; padding: 8px 12px; border: 1px solid var(--border);
                       border-radius: 6px; background: var(--card); color: var(--text);
                       font-size: 14px; }}
  .search-bar button {{ padding: 8px 16px; background: var(--accent); color: #fff;
                        border: none; border-radius: 6px; cursor: pointer; }}
  /* headlines */
  .headline-box {{ background: var(--card); border: 1px solid var(--border);
                   border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
  .headline-box h2 {{ font-size: 15px; margin-bottom: 10px; }}
  .headline-item {{ display: flex; align-items: baseline; gap: 8px; padding: 5px 0;
                    border-bottom: 1px solid var(--border); }}
  .headline-item:last-child {{ border-bottom: none; }}
  .headline-num {{ font-weight: 700; color: var(--accent); min-width: 20px; }}
  .headline-item a {{ color: var(--text); text-decoration: none; flex: 1; }}
  .headline-item a:hover {{ color: var(--accent); text-decoration: underline; }}
  .source-tag {{ font-size: 11px; color: var(--muted); white-space: nowrap; }}
  /* tabs */
  .tab-bar {{ display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 12px; }}
  .tab-btn {{ padding: 7px 14px; background: var(--tab-bg); border: none; border-radius: 6px;
              cursor: pointer; color: var(--text); font-size: 13px; transition: .15s; }}
  .tab-btn.active {{ background: var(--tab-active); color: #fff; font-weight: 600; }}
  .count {{ font-size: 11px; background: rgba(0,0,0,.15); border-radius: 10px;
            padding: 1px 6px; margin-left: 4px; }}
  /* items */
  .section-title {{ font-size: 14px; font-weight: 700; color: var(--muted);
                    text-transform: uppercase; letter-spacing: .5px;
                    margin: 16px 0 8px; border-bottom: 1px solid var(--border); padding-bottom: 4px; }}
  .item-card {{ background: var(--card); border: 1px solid var(--border);
                border-radius: 6px; padding: 12px; margin-bottom: 8px; }}
  .item-card.hidden {{ display: none; }}
  .item-header {{ display: flex; align-items: flex-start; gap: 8px; }}
  .badge {{ font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px;
            white-space: nowrap; }}
  .badge.high {{ background: var(--high); color: #fff; }}
  .badge.mid  {{ background: var(--mid); color: #000; }}
  .badge.low  {{ background: var(--border); color: var(--muted); }}
  .item-title a {{ color: var(--text); font-weight: 500; text-decoration: none; }}
  .item-title a:hover {{ color: var(--accent); text-decoration: underline; }}
  .item-meta {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
  .item-summary {{ font-size: 13px; margin-top: 6px; color: var(--text); }}
  .related {{ font-size: 12px; margin-top: 4px; color: var(--muted); }}
  .related a {{ color: var(--accent); }}
  /* feedback */
  .feedback-note {{ text-align: center; padding: 16px; font-size: 13px; color: var(--muted); }}
  /* search results */
  #search-results {{ display: none; }}
  #search-results.active {{ display: block; }}
</style>
</head>
<body>
<header>
  <h1>📋 소관분야 정책·예산·입법 모니터링</h1>
  <div class="meta">{date} | 총 {len(all_items)}건</div>
</header>
<div class="container">
  {failed_html}
  <div class="search-bar">
    <input type="text" id="search-input" placeholder="🔍 키워드, 부처명, 이슈명으로 검색..." oninput="liveSearch(this.value)">
    <button onclick="clearSearch()">초기화</button>
  </div>
  <div id="search-results"></div>

  <div class="headline-box">
    <h2>🔥 오늘의 헤드라인 Top 5</h2>
    {top5_html or '<p style="color:var(--muted)">오늘의 헤드라인이 없습니다.</p>'}
  </div>

  <div class="tab-bar">{tab_buttons}</div>
  {tab_contents}

  <div class="feedback-note">오늘 놓친 소식이나 불필요했던 항목은 <code>feedback/feedback.md</code>에 기록해 주세요.</div>
</div>

<script>
const ALL_ITEMS = {items_json};

function switchTab(id) {{
  document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + id).style.display = 'block';
  document.getElementById('btn-' + id).classList.add('active');
}}

function liveSearch(q) {{
  const sr = document.getElementById('search-results');
  const tabs = document.querySelector('.tab-bar');
  const tabContents = document.querySelectorAll('.tab-content');
  if (!q.trim()) {{
    sr.innerHTML = ''; sr.style.display = 'none';
    tabs.style.display = ''; tabContents.forEach(el => {{/* restore tab */}});
    switchTab('industry');
    return;
  }}
  const lower = q.toLowerCase();
  const hits = ALL_ITEMS.filter(item =>
    (item.title||'').toLowerCase().includes(lower) ||
    (item.summary||'').toLowerCase().includes(lower) ||
    (item.source||'').toLowerCase().includes(lower) ||
    (item.ministry_name||'').toLowerCase().includes(lower) ||
    (item.ministry||'').toLowerCase().includes(lower)
  );
  tabs.style.display = 'none';
  tabContents.forEach(el => el.style.display = 'none');
  if (!hits.length) {{
    sr.innerHTML = '<p style="color:var(--muted);padding:16px">검색 결과가 없습니다.</p>';
    sr.style.display = 'block'; return;
  }}
  const BADGE = {{'상':'high','중':'mid','하':'low'}};
  sr.innerHTML = '<div class="section-title">검색 결과 (' + hits.length + '건)</div>' +
    hits.map(item => `
      <div class="item-card">
        <div class="item-header">
          <span class="badge ${{BADGE[item.importance]||'low'}}">${{item.importance||'하'}}</span>
          <span class="item-title"><a href="${{item.url}}" target="_blank">${{item.title}}</a></span>
        </div>
        <div class="item-meta">${{item.source}} | ${{item.date}} | ${{item.committee||''}}</div>
        ${{item.summary ? '<div class="item-summary">'+ item.summary.substring(0,150) +'</div>' : ''}}
      </div>`).join('');
  sr.style.display = 'block';
}}

function clearSearch() {{
  document.getElementById('search-input').value = '';
  liveSearch('');
}}
</script>
</body>
</html>'''

    def _render_items(
        self,
        items: list[dict],
        group_by: Optional[str] = 'ministry',
        committee_id: Optional[str] = None,
    ) -> str:
        if not items:
            return '<p style="color:var(--muted);padding:16px">수집된 항목이 없습니다.</p>'

        BADGE = {'상': 'high', '중': 'mid', '하': 'low'}

        if group_by is None:
            return ''.join(self._item_html(item, BADGE) for item in items)

        by_group: dict[str, list[dict]] = defaultdict(list)
        for item in items:
            if group_by == 'institute':
                key = item.get('institute_name') or item.get('source') or '기타'
            else:
                key = item.get('ministry_name') or item.get('ministry') or item.get('source') or '기타'
            by_group[key].append(item)

        group_names = (
            _ordered_keys(by_group, MINISTRY_ORDER.get(committee_id, []))
            if group_by == 'ministry' and committee_id
            else list(by_group.keys())
        )

        html = ''
        for group_name in group_names:
            g_items = by_group[group_name]
            html += f'<div class="section-title">{group_name}</div>'
            html += ''.join(self._item_html(item, BADGE) for item in g_items)
        return html

    def _render_legislation(self, items: list[dict]) -> str:
        """입법 현황도 연구기관과 동일하게 소관 상임위 순서로 묶는다."""
        if not items:
            return '<p style="color:var(--muted);padding:16px">수집된 항목이 없습니다.</p>'

        BADGE = {'상': 'high', '중': 'mid', '하': 'low'}
        by_committee: dict[str, list[dict]] = defaultdict(list)
        for item in items:
            by_committee[item.get('committee') or 'common'].append(item)

        html = ''
        for committee_id in ['industry', 'science', 'agriculture', 'politics']:
            c_items = by_committee.get(committee_id)
            if not c_items:
                continue
            html += f'<div class="section-title" style="font-size:15px;color:var(--accent)">{COMMITTEE_NAMES[committee_id]}</div>'
            html += ''.join(self._item_html(item, BADGE) for item in c_items)
        return html

    def _render_research(self, items: list[dict]) -> str:
        """연구기관 발간물은 소관 상임위 순서(산자→과방→농해수→정무→공통)로 묶고,
        그 안에서 다시 기관별로 묶는다."""
        if not items:
            return '<p style="color:var(--muted);padding:16px">수집된 항목이 없습니다.</p>'

        BADGE = {'상': 'high', '중': 'mid', '하': 'low'}
        by_committee_inst: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for item in items:
            committee = item.get('committee') or 'common'
            inst = item.get('institute_name') or item.get('source') or '기타'
            by_committee_inst[committee][inst].append(item)

        html = ''
        for committee_id in ['industry', 'science', 'agriculture', 'politics', 'common']:
            inst_map = by_committee_inst.get(committee_id)
            if not inst_map:
                continue
            html += f'<div class="section-title" style="font-size:15px;color:var(--accent)">{COMMITTEE_NAMES[committee_id]}</div>'
            for inst, r_items in inst_map.items():
                short = r_items[0].get('institute_short')
                heading = f'{inst}({short})' if short else inst
                html += f'<div class="section-title">{heading}</div>'
                html += ''.join(self._item_html(item, BADGE) for item in r_items)
        return html

    @staticmethod
    def _item_html(item: dict, badge_map: dict) -> str:
        badge_cls = badge_map.get(item.get('importance', '하'), 'low')
        importance = item.get('importance', '하')
        title = item.get('title', '제목 없음')
        url = item.get('url', '#')
        source = item.get('source', '')
        date = item.get('date', '')
        summary = item.get('summary', '')[:150]
        related = item.get('related', [])
        views = item.get('views')
        views_str = f' | 조회수 {views:,}' if isinstance(views, int) else ''
        collected_at = item.get('collected_at', '')
        collected_str = f' | 수집 {collected_at}' if collected_at else ''

        related_html = ''
        if related:
            links = ', '.join(
                f'<a href="{r["url"]}" target="_blank">{r["source"]}</a>'
                for r in related[:3]
            )
            related_html = f'<div class="related">관련 보도: {links}</div>'

        return f'''
<div class="item-card">
  <div class="item-header">
    <span class="badge {badge_cls}">{importance}</span>
    <span class="item-title"><a href="{url}" target="_blank">{title}</a></span>
  </div>
  <div class="item-meta">{source} | 발행일 {date}{views_str}{collected_str}</div>
  {f'<div class="item-summary">{summary}</div>' if summary else ''}
  {related_html}
</div>'''
