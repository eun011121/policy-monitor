"""report_compiler.py — generates the daily report.md.

Structure:
  1. Header (date, run meta)
  2. ⚠️ Failed sources (if any)
  3. 🔥 오늘의 헤드라인 Top 5 (importance=상, 출처 다양성 보장)
  4. Per-committee sections → per-ministry sub-sections → items (상/중 우선, 하 후순위)
  5. 연구기관 발간물 섹션
  6. 공통 이슈 섹션
  7. 입법 현황 섹션
  8. Footer (feedback hint)
"""
import html as html_module
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

COMMITTEE_NAMES = {
    'industry':    '산업통상자원중소벤처기업위원회',
    'science':     '과학기술정보방송통신위원회',
    'agriculture': '농림축산식품해양수산위원회',
    'politics':    '정무위원회',
    'common':      '공통 이슈',
}

# sources/ministries.yaml에 등록된 순서 그대로 — 부처 섹션은 이 순서로 고정 표시한다
# (기존에는 그날 수집된 항목이 어느 부처 것이 먼저 나오느냐에 따라 순서가 들쭉날쭉했음).
MINISTRY_ORDER = {
    'industry':    ['산업통상부', '중소벤처기업부', '지식재산처'],
    'science':     ['과학기술정보통신부', '방송미디어통신위원회', '원자력안전위원회', '우주항공청'],
    'agriculture': ['농림축산식품부', '해양수산부', '농촌진흥청', '산림청', '해양경찰청'],
    'politics':    ['금융위원회', '공정거래위원회', '국무조정실·국무총리비서실', '국민권익위원회'],
}

# 농림부/해수부가 농해수위 양대 핵심 부처 — 산림청은 상대적으로 비중이 낮다는
# 사용자 판단에 따라 '하' 등급 노출 상한을 기본값보다 더 타이트하게 적용한다.
LOW_ITEM_CAP_OVERRIDE = {
    '산림청': 2,
}


def _ordered_ministries(by_ministry: dict, committee_id: str):
    """MINISTRY_ORDER에 있는 부처는 그 순서대로, 목록에 없는 부처(신설/개편 등)는
    끝에 원래 순서 그대로 붙인다 — 순서 정의가 누락돼도 항목이 사라지지 않는다."""
    order = MINISTRY_ORDER.get(committee_id, [])
    ordered = [name for name in order if name in by_ministry]
    leftover = [name for name in by_ministry if name not in order]
    return ordered + leftover

IMPORTANCE_EMOJI = {'상': '🔴', '중': '🟡', '하': '⚪'}

# 부처별 '하' 등급 노출 상한 (가독성 유지) — 상/중 등급은 상한 없이 전부 표시.
# 최근 1주일 범위로 넓힌 뒤로는 부처당 실제 항목 수가 7건을 넘는 경우가 흔해서,
# 상/중까지 잘려나가지 않도록 상한을 '하' 등급에만 적용한다.
MAX_LOW_ITEMS_PER_MINISTRY = 5

_CAPTION_RE = re.compile(
    r'[^\n.!?]*(?:연합뉴스|사진기자단|이미지 제공|제공|사진은 기사와 무관)[^\n.!?]*[.\s]*'
)


def _clean(text: str, max_len: int = 150) -> str:
    """출력 직전 최종 클린업: HTML entity, 사진 캡션, 공백."""
    text = html_module.unescape(text or '')
    text = _CAPTION_RE.sub(' ', text)
    text = re.sub(r'\.([가-힣A-Z])', r'. \1', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    if len(text) > max_len:
        cut = text[:max_len].rsplit(' ', 1)[0]
        text = cut.rstrip('.,') + '…'
    return text


class ReportCompiler:
    def compile(
        self,
        items: list[dict],
        date: str,
        failed_sources: list[str],
        output_path: Path,
    ) -> None:
        lines = []

        # Header
        lines += [
            '# 소관분야 정책·예산·입법 데일리 브리핑',
            '',
            (
                f'**기준일**: {date}  |  '
                f'**생성 시각**: {datetime.now().strftime("%H:%M")}  |  '
                f'**수집 건수**: {len(items)}건'
            ),
            '',
        ]

        # Failed sources warning
        if failed_sources:
            lines += ['## ⚠️ 오늘 수집 실패 소스', '']
            for src in failed_sources:
                lines.append(f'- {src}')
            lines.append('')

        # Top 5 headlines — 출처 다양성: 동일 출처 최대 2건
        top_pool = [i for i in items if i.get('importance') == '상']
        top5: list[dict] = []
        source_count: dict[str, int] = defaultdict(int)
        for item in top_pool:
            src = item.get('source', '')
            if source_count[src] < 2:
                top5.append(item)
                source_count[src] += 1
            if len(top5) >= 5:
                break

        if top5:
            lines += ['## 🔥 오늘의 헤드라인 Top 5', '']
            for idx, item in enumerate(top5, 1):
                item_date = item.get('date', '')
                lines.append(
                    f'{idx}. **[{item["source"]}]** [{item["title"]}]({item["url"]})'
                    f'{f" ({item_date})" if item_date else ""}'
                )
                if item.get('summary'):
                    lines.append(f'   > {_clean(item["summary"], 130)}')
            lines.append('')

        # Separate items by section
        committee_items: dict[str, list[dict]] = defaultdict(list)
        research_items: list[dict] = []
        common_items: list[dict] = []
        legislation_items: list[dict] = []

        for item in items:
            if item['type'] == 'legislation':
                legislation_items.append(item)
            elif item['type'] == 'research':
                # 연구기관 발간물은 소관 상임위와 무관하게 전부 별도 섹션에 모은다
                # (부처 보도자료 "공통 이슈" 섹션과 섞지 않는다 — CLAUDE.md 규칙 9)
                research_items.append(item)
            else:
                committee_items[item.get('committee', 'unknown')].append(item)

        # Committee sections
        for committee_id in ['industry', 'science', 'agriculture', 'politics']:
            c_items = committee_items.get(committee_id, [])
            if not c_items:
                continue
            lines += [f'## {COMMITTEE_NAMES[committee_id]}', '']

            # Group by ministry
            by_ministry: dict[str, list[dict]] = defaultdict(list)
            for item in c_items:
                ministry_name = (
                    item.get('ministry_name') or item.get('ministry') or '기타'
                )
                by_ministry[ministry_name].append(item)

            for ministry_name in _ordered_ministries(by_ministry, committee_id):
                m_items = by_ministry[ministry_name]
                lines.append(f'### {ministry_name}')
                lines.append('')

                # 상/중은 전부 표시, '하'만 상한 적용 (기관별 override 가능)
                low_cap = LOW_ITEM_CAP_OVERRIDE.get(ministry_name, MAX_LOW_ITEMS_PER_MINISTRY)
                priority = [i for i in m_items if i.get('importance') in ('상', '중')]
                low = [i for i in m_items if i.get('importance') == '하']
                shown = priority + low[:low_cap]
                remainder = len(low) - min(len(low), low_cap)

                for item in shown:
                    lines += self._format_item(item)

                if remainder > 0:
                    lines.append(f'  - *(외 {remainder}건 생략 — 중요도 하)*')

                lines.append('')  # 다음 ### 앞 빈 줄 보장

        # Research institute section — 소관 상임위 순서(산자→과방→농해수→정무→공통)로 묶고,
        # 그 안에서 기관별로 다시 묶는다.
        if research_items:
            lines += ['## 📚 연구기관 발간물 (최근 3개월)', '']
            by_committee_inst: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
            for item in research_items:
                committee = item.get('committee') or 'common'
                inst = item.get('institute_name') or item.get('source', '기타')
                by_committee_inst[committee][inst].append(item)
            for committee_id in ['industry', 'science', 'agriculture', 'politics', 'common']:
                inst_map = by_committee_inst.get(committee_id)
                if not inst_map:
                    continue
                lines += [f'### {COMMITTEE_NAMES[committee_id]}', '']
                for inst, r_items in inst_map.items():
                    short = r_items[0].get('institute_short')
                    heading = f'{inst}({short})' if short else inst
                    lines += [f'#### {heading}', '']
                    for item in r_items:
                        lines += self._format_item(item)
                    lines.append('')

        # Common issues (committee 소속이 없는 부처 보도자료)
        common_all = common_items + committee_items.get('common', [])
        if common_all:
            lines += ['## 🌐 공통 이슈 (다부처 거시 주제)', '']
            by_source: dict[str, list[dict]] = defaultdict(list)
            for item in common_all:
                key = item.get('ministry_name') or item.get('institute_short') or item.get('source', '기타')
                by_source[key].append(item)
            for source_name, s_items in by_source.items():
                lines.append(f'### {source_name}')
                lines.append('')
                for item in s_items:
                    lines += self._format_item(item)
                lines.append('')

        # Legislation — 상임위별로 묶어서 표시
        if legislation_items:
            lines += ['## 📋 입법 현황', '']
            by_committee: dict[str, list[dict]] = defaultdict(list)
            for item in legislation_items:
                by_committee[item.get('committee', 'unknown')].append(item)
            for committee_id in ['industry', 'science', 'agriculture', 'politics']:
                c_items = by_committee.get(committee_id)
                if not c_items:
                    continue
                lines += [f'### {COMMITTEE_NAMES[committee_id]}', '']
                for item in c_items:
                    lines += self._format_item(item)
                lines.append('')

        # Footer
        lines += [
            '---',
            '',
            '> 오늘 놓친 소식이 있거나 불필요했던 항목이 있다면 `feedback/feedback.md`에 기록해 주세요.',
            '',
        ]

        output_path.write_text('\n'.join(lines), encoding='utf-8')
        logger.info(f"Report written: {output_path}")

    @staticmethod
    def _format_item(item: dict) -> list[str]:
        emoji = IMPORTANCE_EMOJI.get(item.get('importance', '하'), '⚪')
        title = item.get('title', '제목 없음')
        url = item.get('url', '')
        source = item.get('source', '')
        date = item.get('date', '')
        summary = _clean(item.get('summary', ''))
        importance = item.get('importance', '하')

        lines = []
        if url:
            lines.append(f'- {emoji} **[{importance}]** [{title}]({url})')
        else:
            lines.append(f'- {emoji} **[{importance}]** {title}')
        views = item.get('views')
        views_str = f' | 조회수: {views:,}' if isinstance(views, int) else ''
        collected_at = item.get('collected_at', '')
        collected_str = f' | 수집: {collected_at}' if collected_at else ''
        lines.append(f'  - 출처: {source} | 발행일: {date}{views_str}{collected_str}')
        if summary:
            lines.append(f'  - {summary}')

        related = item.get('related', [])
        if related:
            lines.append(
                f'  - 관련 보도 ({len(related)}건): '
                + ', '.join(f'[{r["source"]}]({r["url"]})' for r in related[:3])
            )
        return lines
