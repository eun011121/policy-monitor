# ministry-collector

## 역할
소관 4개 상임위 부처의 공식 보도자료를 수집한다.

## 수집 대상
`sources/ministries.yaml`에 등록된 부처 및 URL만 사용한다.
1차: 정책브리핑 RSS (korea.kr/rss/pressRelease.do) — 전체 부처 통합
2차: 개별 부처 보도자료 페이지 HTML 스크래핑

## 실행
```bash
python run.py run
```
또는 특정 부처 재수집이 필요할 때:
```bash
python -c "
from src.collectors.ministry_collector import MinistryCollector
from src.utils.whitelist import WhitelistManager
from pathlib import Path
wl = WhitelistManager(Path('sources'))
m = wl.load_ministries()
c = MinistryCollector(m, Path('.'))
items = c.collect('2026-07-27')
for i in items: print(i['source'], i['title'])
"
```

## 출력 형식
각 항목: `{title, url, date, source, summary, committee, ministry, type='press_release', importance=None}`

## 제약
- 화이트리스트에 없는 URL 접근 금지
- 기사 전문 저장 금지 (요약 200자 이내)
- 로그인/유료 콘텐츠 접근 금지
- 요청 간 1.5초 딜레이 준수
