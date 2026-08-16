"""Research institute publication monitor.

최근 3개월 이내 발간된 보고서를 수록한다 (대시보드에서 "연구기관" 탭으로 별도 표시).
- RSS: published_parsed 날짜가 [오늘-3개월, 오늘] 구간인 항목만
- HTML: 행/항목에서 날짜를 파싱하여 같은 구간인 항목만
  날짜를 찾을 수 없는 항목은 제외 (날짜 불명 → 게재 시점 불확실)
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup, Tag

from .base import BaseCollector

logger = logging.getLogger(__name__)

# 날짜 패턴: YYYY-MM-DD, YYYY.MM.DD, YYYY년 MM월 DD일
_DATE_RE = re.compile(
    r'(\d{4})[.\-년][\s]?(\d{1,2})[.\-월][\s]?(\d{1,2})'
)
# 월간지 등 일자 없이 "YYYY-MM"/"YYYY.MM"만 표시하는 사이트용 — 전체 날짜 패턴이
# 안 잡힐 때만 최후 수단으로 시도한다 (예: KIPF 재정포럼 "2026-07").
_MONTH_ONLY_RE = re.compile(r'(?<!\d)(\d{4})[.\-](\d{1,2})(?!\d)')

# 링크 텍스트가 실제 제목이 아니라 "내용보기" 류의 범용 라벨인 사이트가 있음 —
# 이 경우 같은 행 안의 .subject/.title/h3/h4 요소에서 실제 제목을 찾는다.
_GENERIC_LINK_LABELS = {
    '내용보기', '바로가기', '자세히보기', '더보기', '상세보기', 'view', 'more',
    '클릭하면 게시물로 이동합니다.',
}
_JS_ARG_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"|(\d+)")


def _extract_js_args(js_call: str) -> list[str]:
    """JS 함수 호출 문자열에서 인자를 순서대로 추출 (작은따옴표/큰따옴표/숫자 인자 모두 지원)."""
    args = []
    for m in _JS_ARG_RE.finditer(js_call):
        args.append(next(g for g in m.groups() if g is not None))
    return args


def _parse_date(text: str) -> Optional[str]:
    m = _DATE_RE.search(text)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2020 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f'{y:04d}-{mo:02d}-{d:02d}'
        except ValueError:
            pass
    m2 = _MONTH_ONLY_RE.search(text)
    if m2:
        try:
            y, mo = int(m2.group(1)), int(m2.group(2))
            if 2020 <= y <= 2030 and 1 <= mo <= 12:
                return f'{y:04d}-{mo:02d}-01'  # 일자 없는 월간지 — 월초로 근사
        except ValueError:
            pass
    return None


MONTHS_LOOKBACK_DAYS = 90


class ResearchMonitor(BaseCollector):
    item_type = 'research'

    def __init__(self, research: dict, base_dir):
        super().__init__()
        self._research = research
        self._base_dir = base_dir

    # ── public ────────────────────────────────────────────────────────────────

    def collect(self, date: str) -> list[dict]:
        items: list[dict] = []
        since = (
            datetime.strptime(date, '%Y-%m-%d') - timedelta(days=MONTHS_LOOKBACK_DAYS)
        ).strftime('%Y-%m-%d')
        all_institutes = (
            self._research.get('dedicated', []) +
            self._research.get('common', [])
        )
        for inst in all_institutes:
            try:
                new_items = self._collect_institute(inst, since, date)
                items.extend(new_items)
            except Exception as e:
                logger.error(f"ResearchMonitor 실패 [{inst.get('name')}]: {e}")

        logger.info(f"ResearchMonitor: {len(items)}건 수집 (최근 3개월, 기준일 {date})")
        return items

    # ── private ───────────────────────────────────────────────────────────────

    def _collect_institute(self, inst: dict, since: str, date: str) -> list[dict]:
        rss_url = inst.get('rss_url')
        if rss_url:
            items = self._collect_rss(inst, rss_url, since, date)
            if items:
                return items

        pub_url = inst.get('publications_url')
        if not pub_url:
            logger.info(f"  [{inst['short']}] URL 미확정 — 스킵")
            return []
        return self._collect_html(inst, pub_url, since, date)

    # ── RSS ───────────────────────────────────────────────────────────────────

    def _collect_rss(self, inst: dict, rss_url: str, since: str, date: str) -> list[dict]:
        items = []
        feed = feedparser.parse(rss_url)
        if feed.bozo:
            return items
        for entry in feed.entries[:60]:
            pub = self._parse_entry_date(entry)
            if pub is None:
                continue
            pub_str = pub.strftime('%Y-%m-%d')
            if not (since <= pub_str <= date):   # 최근 3개월 이내
                continue
            title = entry.get('title', '').strip()
            url = entry.get('link', '').strip()
            summary = entry.get('summary', '')[:200]
            items.append(self._build_item(inst, title, url, pub_str, summary))
        return items

    # ── HTML ──────────────────────────────────────────────────────────────────

    def _collect_html(self, inst: dict, pub_url: str, since: str, date: str) -> list[dict]:
        soup = self._soup(pub_url)
        if soup is None:
            return []

        # 행(row) 단위로 파싱: 행 전체 텍스트에서 날짜 추출
        items = []
        seen: set[str] = set()
        detail_template = inst.get('detail_url_template')
        js_id_arg_index = inst.get('js_id_arg_index', 0)
        list_selector = inst.get('list_selector')
        date_from_detail = inst.get('date_from_detail', False)

        for row in self._find_rows(soup, pub_url, list_selector):
            row_text = row.get_text(separator=' ', strip=True)
            row_date = _parse_date(row_text)

            # 행 안에 제목 전용 요소(.subject/.title 등)가 있으면 그걸 우선한다 — <a> 자체
            # 텍스트에는 날짜·작성자까지 같이 딸려오는 사이트가 많다(예: KINS 뉴스 카드형 목록).
            # 더 구체적인 셀렉터를 먼저 시도해야 한다(같은 select_one 안에 섞으면 부모 요소가
            # 문서 순서상 먼저 걸려 자식 strong보다 우선돼 버린다).
            # 신뢰도 순으로 단계별 시도한다 — 콤마로 묶은 셀렉터는 "문서 순서상 먼저 나오는
            # 요소"를 고르지 "먼저 적은 셀렉터"를 우선하지 않으므로, 번호 배지 등 엉뚱한
            # 요소(예: KEEI의 <strong class="num">1683</strong>)가 진짜 제목보다 앞에 있으면
            # 잘못 걸릴 수 있다. 전용 제목 클래스를 먼저 시도하고, 범용 태그(strong/h3/h4)는
            # 그것도 없을 때만 최후 수단으로 쓴다.
            title_el = (
                row.select_one('.rpt_title strong') or
                row.select_one('.subject, .title, .tit, .b-title-box p, .item_name') or
                row.select_one('h3, h4, figcaption strong, strong')
            )
            # 제목 요소를 감싸는 실제 <a>가 있으면 그게 진짜 상세보기 링크다 — 이걸 최우선으로
            # 삼는다. onclick이 있다고 무조건 우선시하면 PDF 미리보기/다운로드 버튼(예: KREI의
            # fn_showPdf/fn_download)이 실제 제목 링크보다 먼저 걸려버릴 수 있다(둘 다 onclick을
            # 가진 <a>지만 상세보기 링크가 아님). 제목이 <a> 밖에 있는 카드형 목록(예: KCMI)에서만
            # onclick 우선 탐색으로 폴백한다. 일부 사이트(예: KERI)는 제목 자체가 <a>가 아니라
            # onclick이 붙은 <div>라서(진짜 클릭 대상), 그런 경우 title_el 자신을 링크로 쓴다 —
            # 이때 행 안의 유일한 <a>가 "원문 다운로드" 버튼처럼 별개 기능일 수 있어 위험하다.
            a = (
                (title_el.find_parent('a') if title_el else None) or
                (title_el if title_el and title_el.get('onclick') else None) or
                row.select_one('a[onclick]') or
                row.find('a')
            )
            if not a:
                continue
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            if title in _GENERIC_LINK_LABELS or not title:
                alt = row.select_one('.rpt_title')  # 컨테이너 전체(저자/날짜 포함)라도 없는 것보단 낫다
                if alt:
                    title = alt.get_text(strip=True)
            href = a.get('href', '')

            if href.startswith('javascript:') or href in ('', '#') or href.startswith('#'):
                # JS-driven row: pull the item id out of the JS call and rebuild the
                # detail URL from the institute's template (mirrors ministry_collector).
                # onclick을 href보다 우선한다 — href="javascript:void(0);" 같은 더미와
                # 함께 실제 동작은 onclick에 있는 사이트(KCMI 등)가 있다.
                js_call = a.get('onclick') or row.get('onclick') or href
                args = _extract_js_args(js_call)
                if detail_template and len(args) > js_id_arg_index:
                    href = detail_template.format(id=args[js_id_arg_index])
                else:
                    href = ''
            elif not href.startswith('http'):
                href = urljoin(pub_url, href)

            if not title or not href or title in seen:
                continue

            # 목록에 날짜가 아예 없는 사이트(KDI/KOSBI/KIIP/KISTEP/KIHASA 등)는
            # 상세페이지를 열어서 날짜를 찾는다 — 목록 행 수만큼 추가 요청이 들지만
            # 하루 1회 배치라 감당 가능한 비용이다.
            if row_date is None and date_from_detail:
                row_date = self._fetch_detail_date(href)
            if row_date is None:
                continue                        # 날짜 불명 → 제외
            if not (since <= row_date <= date):
                continue                        # 최근 3개월 밖 → 제외

            seen.add(title)
            items.append(self._build_item(inst, title, href, row_date, ''))

        if items:
            logger.info(f"  [{inst['short']}] 최근 3개월({since}~{date}) {len(items)}건")
        else:
            logger.info(f"  [{inst['short']}] 최근 3개월({since}~{date}) 없음")
        return items

    def _fetch_detail_date(self, url: str) -> Optional[str]:
        """목록에 날짜가 없는 사이트용 — 상세페이지를 열어 본문 앞부분에서 날짜를 찾는다."""
        soup = self._soup(url)
        if soup is None:
            return None
        return _parse_date(soup.get_text(separator=' ', strip=True)[:2000])

    @staticmethod
    def _find_rows(
        soup: BeautifulSoup, pub_url: str, list_selector: Optional[str] = None
    ) -> list[Tag]:
        """발간물 목록의 행(row)에 해당하는 태그 목록을 반환."""
        # 0. 기관별로 실사이트 구조를 확인해 지정해둔 선택자가 있으면 그대로 쓴다 —
        # 공용 후보 목록 순서 때문에 엉뚱한 요소(예: 페이지 어딘가의 오래된 표)가
        # 먼저 매칭돼버리는 사이트(KIET 등)를 위한 탈출구.
        if list_selector:
            return soup.select(list_selector)

        # 1. <table> 기반 게시판
        rows = soup.select('table tbody tr')
        if rows:
            return rows

        # 2. <li> 기반 목록 — 사이트마다 후보 셀렉터가 겹쳐서 엉뚱한 목록(내비게이션 등)까지
        # 섞이지 않도록, 후보를 하나씩 시도해 처음으로 매칭되는 것만 사용한다 (OR로 합치지 않음).
        li_candidates = [
            'ul.board_book.research_report li',
            'ul.report_list li',
            'ul.gallaylist_box li',
            'ul.dataList2 li',
            'ul.thumbnail_lst li',
            'div.board-photo-wrap li',
            'ul.board_list li',
            'ul.pub_list li',
            'div.board_list li',
            'ul.list li',
        ]
        for sel in li_candidates:
            rows = soup.select(sel)
            if rows:
                return rows

        # 3. div/dl 기반 목록
        div_candidates = [
            'dl.board_list dt',
            'div.sign_guide',
            '.list_item',
            '.board_item',
            'article',
        ]
        for sel in div_candidates:
            rows = soup.select(sel)
            if rows:
                return rows

        # 4. 날짜 패턴을 포함하는 부모 태그를 직접 탐색 (최후 수단)
        results = []
        for tag in soup.find_all(['li', 'tr', 'div']):
            if _DATE_RE.search(tag.get_text()):
                results.append(tag)
        return results[:50]

    def _build_item(
        self, inst: dict, title: str, url: str, date: str, summary: str
    ) -> dict:
        return self._fmt_item(
            title=title,
            url=url,
            date=date,
            source=inst['name'],
            summary=summary,
            committee=inst.get('committee', 'common'),
            ministry=inst.get('ministry', ''),
            item_type=self.item_type,
            extra={
                'institute_id': inst['id'],
                'institute_short': inst['short'],
                'institute_name': inst['name'],
            },
        )

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
