"""Importance scorer — assigns 상/중/하 to each item based on rule-based scoring.

Rules:
  상: 소관 상임위 심사/의결 임박 법안·예산안, 예산·재정 규모 명시
  중: 시행령·고시 등 정책 변경, 연구기관 주요 보고서
  하: 단순 동정·행사성 보도자료, 인사

User feedback in feedback.md can add boost/penalty keywords.
"""
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── keyword tables ─────────────────────────────────────────────────────────

HIGH_KEYWORDS = [
    # 법안 심사/의결 — 주의: '심사' 단독은 "국민심사단"/"채용 심사" 등과 충돌하므로
    # 반드시 법안·상임위 맥락의 복합어로만 매치한다.
    '의결', '가결', '부결', '법안심사', '법안 심사', '상임위 심사', '심사보고서',
    '상정', '법안', '개정안', '법률안',
    '예산안', '추경', '추가경정', '세제개편안', '세제개편',
    # 예산·재정 규모
    r'\d+조\s*원', r'\d+억\s*원', '재정지출', '예산 편성', '기금운용계획',
    # 긴급/즉시
    '즉시 시행', '시행 예정', '발효',
    # 부처 업무계획/업무보고 (반기·연간 정책 방향 총괄 발표 — 예산분석과 핵심 관심사)
    '업무계획', '업무보고', '주요업무 추진계획', '정책 방향',
]

MEDIUM_KEYWORDS = [
    '시행령', '시행규칙', '고시', '훈령', '예규', '지침',
    '정책', '계획 수립', '추진 방안', '도입', '개편', '강화', '완화',
    '규제 개선', '허가', '지원 방안', '육성 계획', '활성화',
    '보고서', '분석 보고', '전망', '평가', '연구 결과',
]

LOW_KEYWORDS = [
    '간담회', '행사', '협약', '협력 강화', '방문', '기념식', '축하',
    '임명', '취임', '퇴임', '인사', '위촉', '현장 방문',
    '간행물', '수상', '포럼 참석', '회의 참석', '설명회 개최',
]

# 장관/차관 동정성 보도자료 — 부처 자체 홍보성 콘텐츠라 정책 실질이 없는 경우가 많음.
# "장관/차관 등 직함" + "방문/점검류 동정 동사"가 같이 있으면 강하게 감점한다.
MINISTER_TITLE_MARKERS = ['장관', '차관', '청장', '처장', '위원장', '부총리']
MINISTER_ACTIVITY_VERBS = [
    '방문', '점검', '격려', '위로', '참석', '찾아', '만나', '현장',
    '시찰', '간담회', '동행', '위문', '축사', '테이프', '기념',
]
MINISTER_ACTIVITY_PENALTY = -15

# 채용/공모/인사 등 순수 행정 공지 — 정책·예산·입법과 무관 (예: "해양수산부, 개방형
# 직위 공모"). "공모전"처럼 정책성 공모(청소년 공모전 등)와는 구분하기 위해 채용/직위
# 관련 복합어 위주로만 잡는다.
ADMIN_NOTICE_KEYWORDS = [
    '직위공모', '직위 공모', '경력채용', '경력 채용', '신규채용', '신규 채용',
    '채용공고', '채용 공고', '인턴 채용', '인턴채용', '공무원 채용', '연구원 채용',
    '위원 위촉', '위원 모집', '평가위원 모집', '심사위원 모집', '인사 발령', '전보 인사',
    '위원 공모', '위원공모', '청년위원', '서포터즈 모집', '기자단 모집',
    '홍보대사 모집', '체험단 모집', '모니터링단 모집', '평가단 모집', '심의위 위원',
    '국민심사단', '심사단 모집', '서포터즈', '참가자 모집',
    # R&D 위탁과제 공모/입찰/벤더 선정 등 사업 행정 공고 — 국가 R&D 사업이라는 주제와
    # 무관하게 "공고를 냈다"는 사실 자체는 정책·예산·입법 실질이 아님(예: 항우연의
    # "위탁연구과제 공모", "판매대행 사업 공고" 등)
    '위탁연구과제 공모', '위탁연구과제 재공모', '선정공고', '모집 공고', '사업 공고',
    '참가기업 모집', '경연대회 모집', '위탁과제 공모',
]
ADMIN_NOTICE_PENALTY = -15

# 예능성/트리비아 콘텐츠 — 장차관 등이 유튜브·예능에 출연해 정책과 무관한 가벼운
# 질문에 답하는 류의 콘텐츠. 조회수가 높아도 정책·예산·입법 실질이 없으므로 감점.
TRIVIA_KEYWORDS = [
    '유튜브', '찍먹', '부먹', 'mbti', '먹방', '이상형 월드컵', '밸런스 게임',
    '예능', '토크쇼', '라이프스타일', '먹거리 토크',
]
TRIVIA_PENALTY = -20

# 기관 자체 행사/전시/체험 프로그램 안내 — 정책·예산·입법과 무관한 단순 홍보성 공지
# (예: "국립과천과학관, 8월 14일 특별 야간개관 '과학관의 밤' 개최").
EVENT_NOTICE_KEYWORDS = [
    '야간개관', '특별개관', '체험행사', '전시회 개최', '축제', '한마당',
    '나눔장터', '플리마켓', '과학관의 밤', '문화가 있는 날', '개관 행사',
    '박람회 개최', '전시 개최', '공연 개최',
    # 연구기관·부처 게시판에 섞여 들어오는 단순 행사·이벤트성 PR (발간물/정책과 무관)
    '공모전', '시상식', '백일장', '그림대회', '사생대회', '골든벨', '퀴즈대회',
    '어린이날 행사', '가족 체험', '견학 프로그램',
    # 사회공헌(CSR)·기부·봉사 이벤트 — 연구기관 보도자료 게시판에 발간물과 섞여 들어오는
    # 경우가 많음(예: "핑크빛 응원 올해도 배송 완료", "과학자와 빙수 한 그릇")
    '배송 완료', '기부', '기증', '후원 물품', '위문품', '나눔 행사', '나눔행사',
    '자원봉사', '봉사활동', '연탄 나눔', '김장 나눔', '온정 나눔', '사랑의 열매',
    '한 그릇', '나누는 시간',
    # 내부 행사성 워크숍/설명회/회의 개최 안내 — 다루는 주제가 기술적으로 들려도
    # "개최했다"는 사실 자체는 정책·예산·입법 실질이 아님
    '워크숍 개최', '워크숍을 개최', '설명회 개최', '직원설명회', '회의 개최',
    '포럼 개최',
]

# 아래 카테고리는 사용자가 "하나도 안 중요하다"고 명시적으로 반복 확인한 것들 —
# 단순히 '하' 등급으로 낮추는 정도가 아니라 최종 report/dashboard에서 완전히 제외한다.
# (장차관 동정, 채용/공모 등 행정공지, 예능·트리비아, 기관 자체 행사 안내)

# 이 키워드가 제목+요약에 있으면 소관 외 기사로 간주 → 중요도 크게 감점
OFFTOPIC_PENALTY_KEYWORDS = [
    # 정치 일반 (당내 경쟁, 선거)
    '전당대회', '경선', '지지율', '여론조사', '레임덕', '총선', '대선',
    '성과 가로채기', '압수수색', '특검', '수사', '체포',
    # 외교/안보 (소관 외)
    '비핵화', 'ARF', '김여정', '김정은', '평양', '북한',
    # 연예·스포츠·생활
    '마라톤', '기념 촬영', '팬미팅', '인천공항 이용객', '여름 휴가',
    # 가상자산 투기성
    '비트코인 투자', '코인 투자', '보유하라',
]

OFFTOPIC_PENALTY = -20
LEGISLATION_BOOST = 5


class ImportanceScorer:
    def __init__(self, feedback_path: Optional[Path] = None):
        self._boost_kw: list[str] = []
        self._penalty_kw: list[str] = []
        if feedback_path and feedback_path.exists():
            self._load_feedback(feedback_path)

    # ── public ────────────────────────────────────────────────────────────────

    def score(self, items: list[dict]) -> list[dict]:
        # 같은 부처가 같은 날 참고자료를 3건 이상 함께 낸 경우를 감지한다 — 업무계획/
        # 업무보고 같은 정책 패키지 발표는 보통 개별 제목에 "업무보고"라는 단어 없이 여러
        # 건의 참고자료로 쪼개져 나온다(예: 2026-08-04 산업통상부가 반도체 시행령·통상·
        # 산업정책 등 4건을 같은 날 함께 발표 — 그중 어느 제목에도 "업무보고"가 없었음).
        # 다만 "그냥 바쁜 하루"와 구분하기 위해, 묶음 중 최소 하나는 HIGH_KEYWORDS에
        # 직접 걸리는 "닻(anchor)" 항목이 있어야만 나머지도 함께 승격시킨다.
        cluster_counts = Counter(
            (item.get('ministry'), item.get('date'))
            for item in items
            if item.get('type') == 'press_release' and item.get('ministry')
        )
        anchor_clusters = {
            (item.get('ministry'), item.get('date'))
            for item in items
            if item.get('type') == 'press_release' and self._has_high_signal(item)
        }
        # 여러 매체가 동시에 다루는 이슈는 그 자체로 "관심도 높은 중요 정책" 신호다.
        # 세제개편안처럼 기사마다 제목이 달라(여야 공방/민원/해설 등) 근접 중복 제거로
        # 안 묶이는 경우가 많으므로, HIGH_KEYWORDS 매치 기준으로 서로 다른 언론사가
        # 몇 곳이나 같은 사안을 다뤘는지 별도 집계한다.
        news_kw_sources: dict[str, set] = defaultdict(set)
        for item in items:
            if item.get('type') != 'news':
                continue
            text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()
            src = item.get('source', '')
            for kw in HIGH_KEYWORDS:
                if re.search(kw, text):
                    news_kw_sources[kw].add(src)
        buzzing_keywords = {kw for kw, srcs in news_kw_sources.items() if len(srcs) >= 3}

        kept = []
        excluded_n = 0
        for item in items:
            item['importance'] = self._score_item(
                item, cluster_counts, anchor_clusters, buzzing_keywords
            )
            if (
                self._is_offtopic(item)
                or self._is_known_junk(item)
                or not self._is_substantive(item)
            ):
                excluded_n += 1
                continue
            kept.append(item)
        if excluded_n:
            logger.info(
                f"제외: {excluded_n}건 (입법·정책·예산·재정 실질 신호 없음 — 동정/공모/행사성 등)"
            )
        # 중요도(상→하) 우선, 같은 등급 안에서는 최신 날짜가 위로 오도록 정렬.
        # 안정 정렬이므로 "날짜 내림차순"으로 먼저 정렬한 뒤 "중요도 오름차순"으로
        # 다시 정렬하면, 같은 등급 내 날짜 내림차순 순서가 그대로 유지된다.
        order = {'상': 0, '중': 1, '하': 2}
        kept.sort(key=lambda x: x.get('date', ''), reverse=True)
        kept.sort(key=lambda x: order.get(x['importance'], 9))
        return kept

    # ── private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _has_high_signal(item: dict) -> bool:
        text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()
        return any(re.search(kw, text) for kw in HIGH_KEYWORDS)

    @staticmethod
    def _is_substantive(item: dict) -> bool:
        """개별 '나쁜 키워드'를 계속 나열하는 대신, 반대 방향으로 접근한다 — 입법·정책·
        예산·재정상 실질이 있다고 볼 근거가 하나라도 있는지만 확인하고, 없으면 기본적으로
        제외한다(동정 사진, 위원 공모, 예능 출연, 기관 자체 행사 안내 등은 애초에 이
        근거들 중 어느 것도 충족하지 못하므로 별도 블록리스트 없이 자동으로 걸러진다)."""
        # 입법 현황·연구기관 발간물은 카테고리 자체가 이미 정책 실질을 담보한다
        # 입법현황·연구기관 발간물·부처 공식 보도자료는 이미 화이트리스트+소관위 키워드로
        # 선별을 거친 채널이라, 여기에 다시 "정책 키워드가 문자 그대로 있어야 통과"를
        # 요구하면 정상적인 정책 소식(예: "제2우주센터 건립지 공모에 2개 지자체 응모")까지
        # MEDIUM/HIGH 키워드를 우연히 안 담았다는 이유로 잘려나간다. 이 세 타입은 이미
        # _is_known_junk()/_is_offtopic()로 걸러졌으므로 여기서는 기본 통과시킨다.
        if item.get('type') in ('legislation', 'research', 'press_release'):
            return True

        # 아래부터는 type == 'news' (화이트리스트 언론사의 일반 기사) 전용 — 부처 발표와
        # 달리 소관 여부가 키워드 매칭 하나로만 걸러진 상태라 신호가 약할 수 있으므로
        # 실질 신호를 요구한다.
        text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()

        if any(re.search(kw, text) for kw in HIGH_KEYWORDS):
            return True
        if any(kw in text for kw in MEDIUM_KEYWORDS):
            return True

        # 서로 다른 매체 2곳 이상이 같이 다룬 뉴스 — 실제 파급력이 있다는 신호
        if len(item.get('related') or []) >= 2:
            return True

        return False

    @staticmethod
    def _is_known_junk(item: dict) -> bool:
        """장차관 동정/행정공지/트리비아/기관 자체 행사 안내처럼 반복적으로 확인된
        무의미 패턴은, 조회수가 높거나 여러 매체가 다뤘다는 이유로 구제되지 않도록
        _is_substantive()보다 먼저 걸러낸다(예: 방송대상 국민심사단 모집 공지가
        조회수 2천을 넘겼다고 '상'급으로 올라가는 것을 방지). HIGH_KEYWORDS 실질
        신호가 함께 있을 때만 예외로 살려둔다."""
        text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()
        if any(re.search(kw, text) for kw in HIGH_KEYWORDS):
            return False
        # 부처가 스스로 "(동정)"이라고 태그를 붙인 경우 — 장차관 이름/직함이 제목에
        # 안 나와도 이 태그 자체가 "인사 동정 소식"이라는 명시적 신호다.
        if '(동정)' in text or '[동정]' in text:
            return True
        if (
            any(m in text for m in MINISTER_TITLE_MARKERS)
            and any(v in text for v in MINISTER_ACTIVITY_VERBS)
        ):
            return True
        if any(kw in text for kw in ADMIN_NOTICE_KEYWORDS):
            return True
        if any(kw in text for kw in TRIVIA_KEYWORDS):
            return True
        if any(kw in text for kw in EVENT_NOTICE_KEYWORDS):
            return True
        # "위탁연구과제 2차 재공모"처럼 차수 표기가 중간에 끼어 정확한 복합어로는
        # 안 걸리는 R&D 위탁과제 공모 변형까지 정규식으로 잡는다.
        if re.search(r'위탁\s*(연구)?\s*과제.{0,10}(재)?공모', text):
            return True
        return False

    @staticmethod
    def _is_offtopic(item: dict) -> bool:
        """소관 상임위 관할 밖 주제(외교안보/정치일반/연예 등)는 정책 키워드를 우연히
        포함하더라도 무조건 제외한다."""
        text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()
        return any(kw.lower() in text for kw in OFFTOPIC_PENALTY_KEYWORDS)

    def _score_item(
        self,
        item: dict,
        cluster_counts: Optional[Counter] = None,
        anchor_clusters: Optional[set] = None,
        buzzing_keywords: Optional[set] = None,
    ) -> str:
        text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()

        score = 0

        # 업무계획/업무보고 패키지 발표 감지: 같은 부처가 같은 날 참고자료를 3건 이상
        # 함께 냈고, 그중 최소 하나가 HIGH_KEYWORDS에 직접 걸리는 "닻" 항목이면 —
        # 개별 제목에 "업무보고"라는 단어가 없는 나머지도 같은 패키지로 보고 승격한다.
        # (닻 없이 그냥 건수만 많은 평범한 하루와 구분하기 위한 조건)
        if cluster_counts and item.get('type') == 'press_release':
            key = (item.get('ministry'), item.get('date'))
            if cluster_counts.get(key, 0) >= 3 and anchor_clusters and key in anchor_clusters:
                score += 5

        # 소관 외 기사 강력 감점 (이미 필터를 통과했어도 혹시 남은 것 대비)
        for kw in OFFTOPIC_PENALTY_KEYWORDS:
            if kw.lower() in text:
                score += OFFTOPIC_PENALTY

        # 장관/차관 동정성 콘텐츠 강력 감점 — 부처 홍보용, 정책 실질 없음
        if (
            any(m in text for m in MINISTER_TITLE_MARKERS)
            and any(v in text for v in MINISTER_ACTIVITY_VERBS)
        ):
            score += MINISTER_ACTIVITY_PENALTY

        # 채용/공모/인사 등 순수 행정 공지 강력 감점 — 정책·예산·입법과 무관
        if any(kw in text for kw in ADMIN_NOTICE_KEYWORDS):
            score += ADMIN_NOTICE_PENALTY

        # 예능/트리비아성 콘텐츠 강력 감점 — 조회수 높아도 정책 실질 없음
        if any(kw in text for kw in TRIVIA_KEYWORDS):
            score += TRIVIA_PENALTY

        # 다수 언론사가 함께 다룬 사안 가점 (deduplicator가 채워주는 related 목록 기준) —
        # 조회수와 별개로 "얼마나 널리 보도됐는지"를 반영
        related_count = len(item.get('related') or [])
        if related_count >= 2:
            score += 5
        elif related_count == 1:
            score += 2

        # User feedback boosts/penalties
        for kw in self._boost_kw:
            if kw in text:
                score += 10
        for kw in self._penalty_kw:
            if kw in text:
                score -= 10

        # Low patterns
        for kw in LOW_KEYWORDS:
            if kw in text:
                score -= 3

        # Medium patterns
        for kw in MEDIUM_KEYWORDS:
            if kw in text:
                score += 2

        # High patterns
        for kw in HIGH_KEYWORDS:
            if re.search(kw, text):
                score += 5
                if buzzing_keywords and kw in buzzing_keywords:
                    score += 5  # 서로 다른 언론사 3곳 이상이 동시에 다룬 이슈 추가 가점

        # Item type bonus
        if item.get('type') == 'legislation':
            score += LEGISLATION_BOOST
        elif item.get('type') == 'press_release':
            score += 1

        # 조회수 보너스 (보도자료만 — 부처 게시판에 조회수가 노출되는 경우에 한함).
        # 내용 신호가 전혀 없는데 조회수만으로 '상'까지 올라가지 않도록 보수적으로 가산.
        if item.get('type') == 'press_release':
            views = item.get('views')
            if isinstance(views, int):
                if views >= 1000:
                    score += 5
                elif views >= 300:
                    score += 2

        # 임계값: 상≥10, 중≥3, 하<3 (상 기준 강화 — 확실한 것만 상)
        if score >= 10:
            return '상'
        elif score >= 3:
            return '중'
        else:
            return '하'

    def _load_feedback(self, path: Path) -> None:
        try:
            text = path.read_text(encoding='utf-8')
            for line in text.splitlines():
                line = line.strip()
                if line.upper().startswith('BOOST:'):
                    self._boost_kw.extend(
                        kw.strip().lower()
                        for kw in line.split(':', 1)[1].split(',')
                        if kw.strip()
                    )
                elif line.upper().startswith('PENALTY:'):
                    self._penalty_kw.extend(
                        kw.strip().lower()
                        for kw in line.split(':', 1)[1].split(',')
                        if kw.strip()
                    )
        except Exception as e:
            logger.warning(f"Could not load feedback: {e}")
