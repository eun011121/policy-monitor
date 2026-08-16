# source-list-auditor

## 역할
소관 부처·상임위 목록과 URL을 국회/정부 공식 페이지 기준으로 재검증한다.

## 실행 주기
- **월 1회 정기**: 매월 1일 자동 실행 권장
- **즉시 실행**: 동일 소스 3일 연속 실패 시 자동 플래그

## 검증 항목
1. 소관 부처 명칭 변경 여부 (국회 상임위 홈페이지 기준)
2. 각 부처 보도자료 페이지 URL 접근 가능 여부
3. 연구기관 발간물 페이지 URL 유효성
4. 언론사 RSS URL 응답 확인

## 처리 원칙
- 변경 사항은 자동 반영하지 않고, 리포트 상단에 "이런 조정을 제안합니다 — 승인하시겠어요?" 형태로 먼저 확인
- `sources/*.yaml` 최종 변경은 사용자 승인 후 적용

## 수동 실행
```bash
python -c "
from src.utils.whitelist import WhitelistManager
from pathlib import Path
wl = WhitelistManager(Path('sources'))
m = wl.load_ministries()
for c in m.get('committees', []):
    for min in c.get('ministries', []):
        print(min['name'], min.get('press_url', 'URL 없음'))
"
```
