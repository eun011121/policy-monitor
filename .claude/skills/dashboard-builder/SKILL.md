# dashboard-builder

## 역할
`archive/날짜/report.md` 기반으로 `archive/날짜/dashboard.html`을 생성한다.
로컬 파일을 브라우저에서 더블클릭하면 즉시 열람 가능 (외부 CDN 의존 없음).

## 화면 구성
1. 상단 헤더: 날짜, 총 수집 건수
2. 검색창: 클라이언트-사이드 실시간 필터링
3. 오늘의 헤드라인 Top 5
4. 탭 기반 위원회별 섹션 (산자중기위 / 과방위 / 농해수위 / 정무위 / 공통 이슈 / 입법 현황)
5. 각 항목: 중요도 뱃지 + 제목(링크) + 출처 + 날짜 + 요약 + 관련 보도

## 특징
- 다크모드 자동 지원 (prefers-color-scheme)
- 외부 네트워크 없이 오프라인 동작
- 검색창은 전체 수집 데이터 대상 (JavaScript 클라이언트 사이드)

## 구현 위치
`src/generators/dashboard_builder.py`
