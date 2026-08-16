"""Ministry press-release collector.

Primary:  정책브리핑 RSS (korea.kr) — aggregates all ministry releases.
Fallback: individual ministry HTML pages when portal doesn't cover a ministry.
"""
import html as html_module
import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup

from .base import BaseCollector, REQUEST_DELAY
import time

logger = logging.getLogger(__name__)

_CAPTION_RE = re.compile(
    r'[^\n.!?]*(?:연합뉴스|사진기자단|이미지 제공|제공|사진은 기사와 무관)[^\n.!?]*[.\s]*'
)

# msit.go.kr 보도자료 목록 전용 — 목록이 실제 <table>이 아니라 서버가 값을
# JS unescape() 호출로 인라인 삽입한 뒤 jQuery로 셀에 채워 넣는 방식이라
# BeautifulSoup으로 못 읽는다. 대신 그 JS 블록을 정규식으로 직접 파싱한다.
_MSIT_TITLE_RE = re.compile(r"sHtml\+= unescape\('([^']*)'\);\r?\n\s*sHtml\+= newHtml;")
_MSIT_ID_RE = re.compile(r'fn_detail\((\d+)\)')
_MSIT_DATE_RE = re.compile(r"\$\('#td_'\+'REG_DT'\+'_(\d+)'\)\.html\('([^']*)'\);")
_MSIT_DATE_DOT_RE = re.compile(r'(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})')


def _parse_msit_date(raw: str, fallback: str) -> str:
    """msit.go.kr는 요청 Accept-Language에 따라 REG_DT를 'Aug 7, 2026' 또는
    '2026. 8. 7' 두 형식 중 하나로 내려준다 — 둘 다 처리."""
    raw = raw.strip()
    m = _MSIT_DATE_DOT_RE.match(raw)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return f'{y:04d}-{mo:02d}-{d:02d}'
    try:
        return datetime.strptime(raw, '%b %d, %Y').strftime('%Y-%m-%d')
    except ValueError:
        return fallback


def _clean_summary(text: str, max_len: int = 180) -> str:
    text = html_module.unescape(text)
    text = _CAPTION_RE.sub(' ', text)
    text = re.sub(r'\.([가-힣A-Z])', r'. \1', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    if len(text) > max_len:
        cut = text[:max_len].rsplit(' ', 1)[0]
        text = cut.rstrip('.,') + '…'
    return text


class MinistryCollector(BaseCollector):
    item_type = 'press_release'

    def __init__(self, ministries: dict, base_dir):
        super().__init__()
        self._ministries = ministries
        self._base_dir = base_dir
        self._global_excludes: list[str] = ministries.get('global_exclude', [])
        self._keyword_map = self._build_keyword_map()

    # ── public ────────────────────────────────────────────────────────────────

    def collect(self, date: str) -> list[dict]:
        items: list[dict] = []
        # 부처 보도자료는 최근 1주일 범위로 본다 (오늘 하루로는 놓치는 항목이 많고,
        # 개별 부처 페이지 폴백은 애초에 날짜 필터가 없어 오래된 항목까지 섞여 나왔었음).
        since = datetime.strptime(date, '%Y-%m-%d') - timedelta(days=6)

        # 1. 정책브리핑 RSS (primary)
        portal_url = self._ministries.get('portal', {}).get('rss_url', '')
        if portal_url:
            items.extend(self._collect_portal_rss(portal_url, since, date))

        # 2. Individual ministry HTML pages — 정책브리핑 RSS가 그 부처 기사를 단 1건이라도
        # 실었다고 해서 그 부처를 "커버됐다"며 통째로 건너뛰면 안 된다(예: 포털에 우연히
        # 관련 기사 1건만 잡히고 정작 개별 페이지의 10~20건이 통째로 누락되는 사고가 실제
        # 발생함). 항상 개별 페이지도 긁고, 중복은 Deduplicator(제목 유사도)가 정리한다.
        for committee in self._ministries.get('committees', []):
            for ministry in committee.get('ministries', []):
                url = ministry.get('press_url')
                if not url:
                    continue
                try:
                    if ministry.get('list_api'):
                        new_items = self._collect_ministry_json_api(ministry, committee, since, date)
                    elif ministry.get('custom_parser') == 'msit_inline_js':
                        new_items = self._collect_msit_html(ministry, committee, since, date)
                    else:
                        new_items = self._collect_ministry_html(ministry, committee, since, date)
                    items.extend(new_items)
                except Exception as e:
                    logger.error(f"Ministry HTML scrape failed [{ministry['name']}]: {e}")

        logger.info(f"MinistryCollector: {len(items)} items for {date}")
        return items

    # ── private ───────────────────────────────────────────────────────────────

    def _collect_portal_rss(self, rss_url: str, since: datetime, date: str) -> list[dict]:
        items = []
        feed = feedparser.parse(rss_url)
        if feed.bozo:
            logger.warning(f"Portal RSS parse warning: {feed.bozo_exception}")

        for entry in feed.entries:
            pub = self._parse_entry_date(entry)
            if pub and pub < since:
                continue
            pub_str = pub.strftime('%Y-%m-%d') if pub else date

            title = entry.get('title', '').strip()
            url = entry.get('link', '').strip()
            raw_summary = entry.get('summary', '').strip()
            plain = BeautifulSoup(raw_summary, 'lxml').get_text(separator=' ') if raw_summary else ''
            summary = _clean_summary(plain)

            text = title + ' ' + summary

            if self._is_globally_excluded(text):
                continue

            committee_id, ministry_id, ministry_name = self._classify(text)
            if not committee_id:
                continue

            items.append(self._fmt_item(
                title=title,
                url=url,
                date=pub_str,
                source='정책브리핑',
                summary=summary,
                committee=committee_id,
                ministry=ministry_id,
                item_type=self.item_type,
                extra={'ministry_name': ministry_name},
            ))
        return items

    def _collect_ministry_json_api(
        self, ministry: dict, committee: dict, since: datetime, date: str
    ) -> list[dict]:
        """게시판 목록이 AJAX(JSON)로 렌더링되는 부처용 — 내부 API를 직접 호출한다.

        sources/ministries.yaml의 `list_api` 설정(url/params/data_path/필드명)을 사용.
        """
        items = []
        api = ministry['list_api']
        resp = self._post(
            api['url'], data=api.get('params', {}),
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        if resp is None:
            logger.warning(f"Could not fetch {ministry['name']} press page (json api)")
            return items

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"{ministry['name']}: JSON 파싱 실패: {e}")
            return items

        node = data
        for key in api.get('data_path', '').split('.'):
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        rows = node if isinstance(node, list) else []

        detail_template = ministry.get('detail_url_template', '')
        title_field = api.get('title_field', 'SUBJECT')
        date_field = api.get('date_field', 'WRITE_DATE')
        id_field = api.get('id_field', 'BBS_SEQ')
        views_field = api.get('views_field')

        for row in rows[:20]:
            title = str(row.get(title_field) or '').strip()
            raw_date = str(row.get(date_field) or '')
            pub_str = raw_date.replace('.', '-').rstrip('-') or date
            item_id = row.get(id_field)
            href = detail_template.format(id=item_id) if detail_template and item_id else ''
            if not title or not href:
                continue
            if not (since.strftime('%Y-%m-%d') <= pub_str[:10] <= date):
                continue  # 최근 1주일 밖 → 제외
            views = None
            if views_field is not None:
                raw_views = row.get(views_field)
                if isinstance(raw_views, int):
                    views = raw_views
                elif isinstance(raw_views, str) and raw_views.replace(',', '').isdigit():
                    views = int(raw_views.replace(',', ''))
            items.append(self._fmt_item(
                title=title,
                url=href,
                date=pub_str,
                source=ministry['name'],
                summary='',
                committee=committee['id'],
                ministry=ministry['id'],
                item_type=self.item_type,
                extra={'ministry_name': ministry['name'], 'views': views},
            ))
        return items

    def _collect_msit_html(
        self, ministry: dict, committee: dict, since: datetime, date: str
    ) -> list[dict]:
        """msit.go.kr 전용 파서 — 인라인 JS unescape() 블록에서 제목/날짜/ID를 추출."""
        items = []
        resp = self._get(ministry['press_url'])
        if resp is None:
            logger.warning(f"Could not fetch {ministry['name']} press page")
            return items
        resp.encoding = resp.apparent_encoding or 'utf-8'
        text = resp.text

        ids = _MSIT_ID_RE.findall(text)
        titles = _MSIT_TITLE_RE.findall(text)
        dates: dict[int, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('//'):
                continue
            m = _MSIT_DATE_RE.match(stripped)
            if m:
                dates[int(m.group(1))] = m.group(2)

        detail_template = ministry.get('detail_url_template', '')
        for i, title in enumerate(titles):
            if i >= len(ids) or not title:
                continue
            pub_str = _parse_msit_date(dates.get(i, ''), date)
            href = detail_template.format(id=ids[i]) if detail_template else ''
            if not href:
                continue
            if not (since.strftime('%Y-%m-%d') <= pub_str <= date):
                continue  # 최근 1주일 밖 → 제외
            items.append(self._fmt_item(
                title=title,
                url=href,
                date=pub_str,
                source=ministry['name'],
                summary='',
                committee=committee['id'],
                ministry=ministry['id'],
                item_type=self.item_type,
                extra={'ministry_name': ministry['name']},
            ))
        return items

    def _collect_ministry_html(
        self, ministry: dict, committee: dict, since: datetime, date: str
    ) -> list[dict]:
        items = []
        press_url = ministry['press_url']
        soup = self._soup(press_url)
        if soup is None:
            logger.warning(f"Could not fetch {ministry['name']} press page")
            return items

        list_selector = ministry.get('list_selector')
        if list_selector:
            rows = soup.select(list_selector)
        else:
            rows = soup.select('table tbody tr')
            if not rows:
                # <table> 없는 게시판(예: 재정경제부 ul.boardType3) 폴백
                rows = soup.select('ul.boardType3 li, ul.board_list li')
        if not rows:
            return items

        detail_template = ministry.get('detail_url_template')
        js_id_arg_index = ministry.get('js_id_arg_index', 0)
        title_selector = ministry.get('title_selector')
        date_selector = ministry.get('date_selector', '.date, .day')

        seen_hrefs: set[str] = set()
        for row in rows[:20]:
            # 제목 셀이 class에 "title"을 포함하면 그 안에서만 찾는다 — 그렇지 않으면
            # 첨부파일 다운로드 등 행 내 다른 <a>가 먼저 잡히는 사이트가 있다 (예: KASA).
            title_td = row.find('td', class_=lambda c: c and 'title' in c)
            scope = title_td or row
            a = scope.find('a') or scope.find('button')
            if not a:
                continue
            # <table> 없는 카드형 목록은 <a> 하나가 제목+요약+날짜를 통째로 감싸는 경우가
            # 많다(예: 농진청/산림청) — title_selector가 지정돼 있으면 그 안의 텍스트만 쓴다.
            title_el = row.select_one(title_selector) if title_selector else None
            title_raw = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            # 아이콘/배지 텍스트("[본청]" 등)와 실제 제목 사이에 줄바꿈·탭이 그대로
            # 남아있는 사이트가 있다(예: 산림청) — get_text(strip=True)는 양끝만 자르므로
            # 내부 공백은 별도로 접어준다.
            title = re.sub(r'\s+', ' ', title_raw).strip()
            href = a.get('href', '')

            if href.startswith('javascript:') or href in ('', '#', '#none', '#view'):
                # JS-driven row: pull the item id out of the JS call — either inline in
                # href="javascript:..." or in an onclick on the <a>/<button>/<tr> — and
                # rebuild the detail URL from the ministry's template.
                # onclick을 href보다 우선한다 — href="javascript:void(0);" 같은 더미와
                # 함께 실제 동작은 onclick에 있는 사이트가 있다.
                js_call = a.get('onclick') or row.get('onclick') or href
                args = re.findall(r"'([^']*)'", js_call)
                if detail_template and len(args) > js_id_arg_index:
                    href = detail_template.format(id=args[js_id_arg_index])
                else:
                    href = ''
            elif not href.startswith('http'):
                href = urljoin(press_url, href)

            # JS-driven list pages: all links resolve to same URL → skip entire batch
            if href:
                seen_hrefs.add(href.split('#')[0])

            date_td = row.find_all('td')
            pub_str = date
            date_td_index = None
            if len(date_td) >= 2:
                for idx, td in enumerate(date_td):
                    txt = td.get_text(strip=True)
                    # 부처마다 날짜 구분자가 다르다 — "2026-08-04" / "2026.08.04" /
                    # "2026.08.04." 전부 지원 (대시만 인식하면 점(.) 형식인 부처는
                    # 전부 "오늘" 날짜로 잘못 찍힌다 — 실제 MSS에서 발생했던 버그).
                    m = re.fullmatch(r'(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})\.?', txt)
                    if m:
                        y, mo, d = (int(g) for g in m.groups())
                        pub_str = f'{y:04d}-{mo:02d}-{d:02d}'
                        date_td_index = idx
                        break
            # <td> 없는 카드형 게시판이거나, <td>는 있지만 날짜가 셀 전체가 아니라 다른
            # 텍스트와 함께 섞여 있어(예: 농림축산식품부 dd.date) 위 fullmatch가 못 찾은
            # 경우 모두 여기서 .date/.day류 클래스로 재시도한다 (요청 시 date_selector로 override).
            if date_td_index is None:
                date_el = row.select_one(date_selector)
                if date_el:
                    m = re.search(r'(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})', date_el.get_text(strip=True))
                    if m:
                        y, mo, d = (int(g) for g in m.groups())
                        pub_str = f'{y:04d}-{mo:02d}-{d:02d}'

            # 조회수 추정: 첫 번째(번호)·날짜 셀을 제외한 순수 숫자 셀 중 마지막 것.
            # 조회수 컬럼이 없는 사이트에서는 안전하게 None으로 남는다.
            views = None
            if len(date_td) >= 3:
                for idx, td in enumerate(date_td):
                    if idx == 0 or idx == date_td_index:
                        continue
                    txt = td.get_text(strip=True).replace(',', '')
                    if txt.isdigit():
                        views = int(txt)

            if not title or not href:
                continue
            if not (since.strftime('%Y-%m-%d') <= pub_str <= date):
                continue  # 최근 1주일 밖 → 제외
            items.append(self._fmt_item(
                title=title,
                url=href,
                date=pub_str,
                source=ministry['name'],
                summary='',
                committee=committee['id'],
                ministry=ministry['id'],
                item_type=self.item_type,
                extra={'ministry_name': ministry['name'], 'views': views},
            ))

        # 모든 링크가 동일 URL → JS 네비게이션 목록 페이지 (개별 URL 파싱 불가)
        if len(seen_hrefs) == 1 and items:
            only_href = next(iter(seen_hrefs))
            # seen_hrefs에는 #anchor를 제거한 href가 들어있음
            # press_url과 동일(= 목록 페이지 URL)이면 개별 기사 URL이 아님
            if only_href == press_url or only_href == press_url.rstrip('/'):
                logger.warning(
                    f"[{ministry['name']}] HTML scraper: 모든 링크가 목록 페이지 URL로 동일 "
                    f"— JS 네비게이션으로 판단, 해당 항목 제외"
                )
                return []

        return items

    def _is_globally_excluded(self, text: str) -> bool:
        for kw in self._global_excludes:
            if kw in text:
                return True
        return False

    def _build_keyword_map(self) -> list[tuple[list[str], list[str], str, str, str]]:
        """Returns list of (keywords, exclude_kws, committee_id, ministry_id, ministry_name).
        정책브리핑 분류는 strict_keywords(구체적 복합어) 우선 사용.
        """
        mapping = []
        for committee in self._ministries.get('committees', []):
            for ministry in committee.get('ministries', []):
                # strict_keywords 우선, 없으면 keywords 폴백
                keywords = (
                    ministry.get('strict_keywords') or
                    ministry.get('keywords', [ministry['name']])
                )
                excl = ministry.get('exclude_keywords', [])
                mapping.append((
                    keywords, excl,
                    committee['id'], ministry['id'], ministry['name'],
                ))
        return mapping

    def _classify(
        self, text: str
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        for keywords, excl_kws, committee_id, ministry_id, ministry_name in self._keyword_map:
            for kw in keywords:
                if not kw:
                    continue
                if kw in text:
                    if any(ex in text for ex in excl_kws):
                        break  # 이 부처 제외, 다음 부처로
                    return committee_id, ministry_id, ministry_name
        return None, None, None

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
