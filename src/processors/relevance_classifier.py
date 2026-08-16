"""AI-based relevance classifier using Claude API.

2-stage pipeline:
  Stage 1: loose keyword filter (wide net) → candidates
  Stage 2: Claude Haiku batch judgment → relevant items only

If ANTHROPIC_API_KEY is not set, falls back to keyword-only mode.
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# 배치당 최대 기사 수 (Claude 컨텍스트 제한 고려)
BATCH_SIZE = 60

# 판단에 사용할 모델 (비용 대비 성능: Haiku)
MODEL = 'claude-haiku-4-5-20251001'

# 위원회별 소관 영역 설명 (Claude에 줄 컨텍스트)
COMMITTEE_CONTEXT = """
소관 위원회 및 부처 정보 (대한민국 22대 국회 기준):

[산자중기위] 산업통상자원중소벤처기업위원회
- 산업통상자원부: 에너지 정책, 전력/가스/석유 수급, 통상·무역 정책, 제조업 지원, 수출입, 자유무역협정(FTA), 산업단지, 전기차·배터리
- 중소벤처기업부: 중소기업 지원, 벤처·스타트업 정책, 소상공인 지원, 창업 지원, 소상공인 금융, K-브랜드
- 특허청: 특허·상표·디자인 등록, 지식재산권 보호

[과방위] 과학기술정보방송통신위원회
- 과학기술정보통신부: R&D 예산·정책, 국가 AI 전략, 반도체 정책, ICT 산업 정책, 사이버보안, 연구개발 투자, 디지털 플랫폼, 6G, 양자컴퓨팅, 클라우드 정책
- 방송통신위원회: OTT·미디어 규제, 방송법 개정, KBS 수신료, 공영방송 지배구조, 유료방송 정책
- 원자력안전위원회: 원전 안전 심사, 방사선 안전, 원자로 인허가, 방사성 폐기물 관리
- 우주항공청: 우주발사체(누리호), 위성 개발, 달 탐사, 우주항공 산업 정책

[농해수위] 농림축산식품해양수산위원회
- 농림축산식품부: 농업 정책, 축산 정책, 식량 안보, 쌀값·농산물 수급, 가축질병, 식품 안전, 농촌 개발
- 해양수산부: 수산업 정책, 항만 개발, 해운 정책, 어업 규제, 해양환경, 해수부 R&D
- 농촌진흥청: 농업 기술 개발, 품종 개발
- 산림청: 산불 예방·대응 정책, 임업 정책, 산림 자원, 탄소흡수원

[정무위] 정무위원회
- 금융위원회: 금융 규제, 자본시장법, 은행·보험·증권 정책, 핀테크 규제, PF 대출 정책, 가계부채 관리, 공매도 정책, 가상자산법
- 공정거래위원회: 독점 규제, 담합 조사, 플랫폼 규제, 대기업집단 지정, 불공정거래 조사, 가맹점 규제
- 국무조정실: 국무총리 주재 국정 조정, 다부처 협업 정책, 범정부 대응
- 국민권익위원회: 공직자 청렴, 부패 방지, 고충 민원 처리
"""

# 제외해야 하는 기사 유형 안내
EXCLUSION_GUIDE = """
다음 기사는 소관 외로 판단하여 제외하세요:
- 정당 내부 경선·전당대회·지지율·레임덕 등 순수 정치 기사
- 국내와 무관한 외국 사건 (유럽 산불, 외국 대통령 발언 등) — 단, 한국 정부의 국제 통상·외교 정책 기사는 포함
- 연예인·스포츠·생활 정보
- 특검·수사·체포 등 사법 기사 (금융 규제 위반으로 당국이 조치하는 경우는 포함)
- 기업 실적 발표 (단, 정부 정책이 원인이거나 결과에 영향을 주는 경우는 포함)
"""


class RelevanceClassifier:
    """Claude API를 사용해 기사의 소관 위원회/부처를 판별한다."""

    def __init__(self, ministries: dict):
        self._api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        self._ministries = ministries
        self._ministry_lookup = self._build_ministry_lookup(ministries)

    def is_available(self) -> bool:
        return bool(self._api_key)

    def classify(self, items: list[dict]) -> list[dict]:
        """기사 목록에서 소관 기사만 추출하여 위원회/부처 정보 설정 후 반환."""
        if not self.is_available():
            logger.info("RelevanceClassifier: API 키 없음 → 키워드 필터 결과 그대로 사용")
            return items

        if not items:
            return items

        results: list[dict] = []
        for batch_start in range(0, len(items), BATCH_SIZE):
            batch = items[batch_start: batch_start + BATCH_SIZE]
            try:
                classified = self._classify_batch(batch)
                results.extend(classified)
            except Exception as e:
                logger.error(f"RelevanceClassifier 배치 실패: {e}")
                # 실패 시 해당 배치 원본 그대로 사용
                results.extend(batch)

        logger.info(
            f"RelevanceClassifier: {len(items)}건 → {len(results)}건 "
            f"(제외 {len(items) - len(results)}건)"
        )
        return results

    # ── private ───────────────────────────────────────────────────────────────

    def _classify_batch(self, items: list[dict]) -> list[dict]:
        from anthropic import Anthropic
        client = Anthropic(api_key=self._api_key)

        # 기사 목록 텍스트 작성
        articles_text = self._format_articles(items)

        prompt = f"""{COMMITTEE_CONTEXT}

{EXCLUSION_GUIDE}

아래 기사 목록을 보고, 각 기사가 위 소관 위원회의 정책·예산·입법 모니터링 대상인지 판단하세요.

판단 기준:
- 해당 위원회 소관 부처의 정책, 규제, 예산, 법령, 계획과 관련 있으면 포함
- 부처명이 직접 언급되지 않아도 맥락상 소관 영역이 명확하면 포함
- 소관 외이거나 판단 불가이면 제외

---기사 목록---
{articles_text}
---끝---

각 기사에 대해 아래 JSON 배열 형식으로만 응답하세요. 다른 텍스트는 쓰지 마세요.
[
  {{
    "idx": 1,
    "relevant": true,
    "committee": "industry",
    "ministry_id": "motie",
    "ministry_name": "산업통상자원부",
    "reason": "산업부의 에너지 전환 정책과 직접 관련"
  }},
  {{
    "idx": 2,
    "relevant": false,
    "committee": null,
    "ministry_id": null,
    "ministry_name": null,
    "reason": "정당 경선 기사로 소관 외"
  }}
]

committee 값: "industry" | "science" | "agriculture" | "politics" 중 하나
relevant=false인 경우 committee/ministry_id/ministry_name은 null로 설정
"""

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{'role': 'user', 'content': prompt}],
        )

        raw = response.content[0].text.strip()
        decisions = self._parse_response(raw, len(items))

        relevant_items = []
        for i, item in enumerate(items):
            decision = decisions.get(i + 1, {})
            if not decision.get('relevant', True):
                logger.debug(
                    f"  [제외] {item['title'][:40]} — {decision.get('reason', '')}"
                )
                continue

            # AI 판단으로 위원회/부처 정보 업데이트
            if decision.get('committee'):
                item['committee'] = decision['committee']
            if decision.get('ministry_id'):
                item['ministry'] = decision['ministry_id']
            if decision.get('ministry_name'):
                item['ministry_name'] = decision['ministry_name']
            if decision.get('reason'):
                item['relevance_reason'] = decision['reason']

            relevant_items.append(item)

        return relevant_items

    @staticmethod
    def _format_articles(items: list[dict]) -> str:
        lines = []
        for i, item in enumerate(items, 1):
            title = item.get('title', '')
            summary = (item.get('summary', '') or '')[:200]
            lines.append(f"[{i}] 제목: {title}")
            if summary:
                lines.append(f"    요약: {summary}")
        return '\n'.join(lines)

    @staticmethod
    def _parse_response(raw: str, count: int) -> dict[int, dict]:
        """JSON 응답을 파싱. 실패 시 빈 dict 반환 (원본 유지)."""
        # JSON 배열 추출 (Claude가 가끔 앞뒤에 텍스트 추가할 수 있음)
        start = raw.find('[')
        end = raw.rfind(']')
        if start == -1 or end == -1:
            logger.warning("RelevanceClassifier: JSON 배열을 찾지 못함")
            return {}
        try:
            data = json.loads(raw[start: end + 1])
            return {item['idx']: item for item in data if 'idx' in item}
        except json.JSONDecodeError as e:
            logger.warning(f"RelevanceClassifier: JSON 파싱 실패 — {e}")
            return {}

    @staticmethod
    def _build_ministry_lookup(ministries: dict) -> dict[str, tuple[str, str]]:
        """ministry_id → (committee_id, ministry_name) 매핑."""
        lookup = {}
        for committee in ministries.get('committees', []):
            for m in committee.get('ministries', []):
                lookup[m['id']] = (committee['id'], m['name'])
        return lookup
