"""Legislative tracker — queries the National Assembly Open API (open.assembly.go.kr).

API: 열린국회정보 의안정보 (nzmimeepazxkubdpn)
  https://open.assembly.go.kr/portal/openapi/nzmimeepazxkubdpn

If no API key is configured, falls back to each standing committee's own site
(예: industry.na.go.kr) — 국회 의안정보시스템(likms.assembly.go.kr)의 상임위별
법안검색 URL이 2026년 기준 죽어 있어, 각 위원회 사이트의 "의안 > 계류의안현황"이
호출하는 내부 JSON API(findStatList.json)를 직접 사용한다. 세션 쿠키 + Spring
Security CSRF 토큰(meta[name=_csrf])이 필요하며 로그인은 불필요.

Set ASSEMBLY_API_KEY in environment or api_key field in sources/ministries.yaml.
"""
import logging
import os
import re
from datetime import datetime, timedelta

from .base import BaseCollector

logger = logging.getLogger(__name__)

# 법안 발의는 뉴스처럼 매일 나오는 게 아니라 하루치 창으로 보면 대부분 0건으로
# 나온다 — 부처 보도자료(7일)보다 더 넓게, 2주 창으로 본다.
LEGISLATION_LOOKBACK_DAYS = 30

OPEN_API_BASE = 'https://open.assembly.go.kr/portal/openapi/nzmimeepazxkubdpn'

# 상임위 자체 사이트 — 22대(2024~2028) 국회 기준. cmmitCd는 findStatList.json에
# 넘기는 위원회 전체 명칭(공식 명칭과 정확히 일치해야 함).
COMMITTEE_SITES = {
    'industry':    ('https://industry.na.go.kr', '산업통상자원중소벤처기업위원회'),
    'science':     ('https://science.na.go.kr', '과학기술정보방송통신위원회'),
    'agriculture': ('https://agri.na.go.kr', '농림축산식품해양수산위원회'),
    'politics':    ('https://policy.na.go.kr', '정무위원회'),
}
BILL_LIST_PAGE = '/cmmit/bill/cntsBill/list.do?menuNo=2000079'
BILL_LIST_API = '/cmmit/bill/cntsBill/findStatList.json'
_CSRF_RE = re.compile(r'<meta name="_csrf" content="([^"]+)"')

# Open API(ASSEMBLY_API_KEY 설정 시)용 22대 국회 상임위원회 코드 — COMMITTEE_SITES와 별개 체계
OPEN_API_COMMITTEE_CODES = {
    'industry':    '9770772',
    'science':     '9770771',
    'agriculture': '9770770',
    'politics':    '9770762',
}


class LegislativeTracker(BaseCollector):
    item_type = 'legislation'

    def __init__(self, ministries: dict, base_dir):
        super().__init__()
        self._ministries = ministries
        self._base_dir = base_dir
        self._api_key = os.environ.get('ASSEMBLY_API_KEY', '')

    # ── public ────────────────────────────────────────────────────────────────

    def collect(self, date: str) -> list[dict]:
        items: list[dict] = []
        since = datetime.strptime(date, '%Y-%m-%d') - timedelta(days=LEGISLATION_LOOKBACK_DAYS)

        for committee_id, (base_url, cmmit_name) in COMMITTEE_SITES.items():
            try:
                if self._api_key:
                    new_items = self._collect_open_api(
                        committee_id, OPEN_API_COMMITTEE_CODES.get(committee_id, ''), since, date
                    )
                else:
                    new_items = self._collect_committee_site(committee_id, base_url, cmmit_name, since, date)
                items.extend(new_items)
            except Exception as e:
                logger.error(f"LegislativeTracker failed [{committee_id}]: {e}")

        logger.info(f"LegislativeTracker: {len(items)} items for {date}")
        return items

    # ── Open API ──────────────────────────────────────────────────────────────

    def _collect_open_api(
        self, committee_id: str, committee_code: str, since: datetime, date: str
    ) -> list[dict]:
        import json
        items = []
        params = {
            'KEY': self._api_key,
            'Type': 'json',
            'pIndex': 1,
            'pSize': 50,
            'COMMITTEE_CODE': committee_code,
        }
        resp = self._get(OPEN_API_BASE, params=params)
        if resp is None:
            return items

        try:
            data = resp.json()
            rows = (
                data.get('nzmimeepazxkubdpn', [{}])[1]
                    .get('row', [])
            )
        except Exception as e:
            logger.warning(f"Open API JSON parse failed: {e}")
            return items

        for row in rows:
            propose_dt = row.get('PROPOSE_DT', '')
            if propose_dt and propose_dt < since.strftime('%Y%m%d'):
                continue
            pub_str = f"{propose_dt[:4]}-{propose_dt[4:6]}-{propose_dt[6:8]}" if len(propose_dt) == 8 else date

            title = row.get('BILL_NAME', '').strip()
            bill_id = row.get('BILL_ID', '')
            url = f"https://likms.assembly.go.kr/bill/billDetail.do?billId={bill_id}" if bill_id else ''
            status = row.get('PROC_RESULT_CD', '') or row.get('CURR_COMMITTEE', '')
            proposer = row.get('PROPOSER', '')

            items.append(self._fmt_item(
                title=title,
                url=url,
                date=pub_str,
                source='국회 의안정보시스템',
                summary=f"발의: {proposer} | 상태: {status}",
                committee=committee_id,
                ministry='',
                item_type=self.item_type,
                extra={'bill_id': bill_id, 'status': status, 'proposer': proposer},
            ))
        return items

    # ── 상임위 사이트 폴백 ────────────────────────────────────────────────────────

    def _collect_committee_site(
        self, committee_id: str, base_url: str, cmmit_name: str, since: datetime, date: str
    ) -> list[dict]:
        """각 상임위 자체 사이트(예: industry.na.go.kr)의 계류의안 목록 JSON API 호출.

        likms.assembly.go.kr의 상임위별 의안검색 URL이 죽어 있어 대체 수단으로 사용.
        위원회 사이트는 Spring Security CSRF를 쓰므로, 목록 페이지를 먼저 GET해
        세션 쿠키와 meta[name=_csrf] 토큰을 얻은 뒤 findStatList.json에 POST한다.
        """
        items: list[dict] = []

        list_page_resp = self._get(base_url + BILL_LIST_PAGE)
        if list_page_resp is None:
            logger.warning(f"LegislativeTracker [{committee_id}]: 목록 페이지 접속 실패")
            return items

        m = _CSRF_RE.search(list_page_resp.text)
        if not m:
            logger.warning(f"LegislativeTracker [{committee_id}]: CSRF 토큰을 찾지 못함")
            return items
        csrf_token = m.group(1)

        data = {
            'pageUnit': '60',
            'cmmitCd': cmmit_name,
            'passDivCd': 'G',   # 계류의안(심사 진행 중) — 신규 발의 추적에 적합
            'procDivCd': '',
            'billKindCd': '',   # 빈 값 = 법률안 외 전체 의안 종류 포함
            'proposerKindCd': '',
            'pageIndex': '1',
            'searchWord': '',
            'pageLink': 'fnSearchStatList',
        }
        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': base_url + BILL_LIST_PAGE,
            'X-CSRF-TOKEN': csrf_token,
        }
        resp = self._post(base_url + BILL_LIST_API, data=data, headers=headers)
        if resp is None:
            return items

        try:
            result_list = resp.json().get('resultList', [])
        except Exception as e:
            logger.warning(f"LegislativeTracker [{committee_id}]: JSON 파싱 실패: {e}")
            return items

        since_str = since.strftime('%Y-%m-%d')
        for row in result_list:
            propose_dt = row.get('proposeDt', '') or ''
            if propose_dt and propose_dt < since_str:
                continue

            title = (row.get('billName') or '').strip()
            bill_id = row.get('billId', '')
            url = row.get('billLinkUrl', '')
            proposer_kind = row.get('proposerKindCd', '')
            proc_stage = row.get('procStageCd', '')
            summary = ' | '.join(
                p for p in (
                    f"{proposer_kind} 발의" if proposer_kind else '',
                    f"상태: {proc_stage}" if proc_stage else '',
                ) if p
            )

            items.append(self._fmt_item(
                title=title,
                url=url,
                date=propose_dt or date,
                source='국회 의안정보시스템',
                summary=summary,
                committee=committee_id,
                ministry='',
                item_type=self.item_type,
                extra={'bill_id': bill_id, 'status': proc_stage, 'proposer': proposer_kind},
            ))
        return items
