# history-search

## 역할
`index/history-index.json` 및 `archive/` 전체를 대상으로 과거 아카이브를 검색한다.

## 사용 방법
```bash
# 키워드 검색
python run.py search "반도체 지원"

# 부처 필터
python run.py search "보조금" --ministry 중소벤처기업부

# 위원회 + 기간 필터
python run.py search "법안" --committee science --start 2026-07-01 --end 2026-07-27
```

## 지원 필터
- `query`: 제목·요약·출처 대상 텍스트 검색
- `--ministry`: 부처명 부분 일치
- `--committee`: industry / science / agriculture / politics
- `--start` / `--end`: 날짜 범위 (YYYY-MM-DD)

## 출력
중요도 내림차순, 날짜 내림차순 정렬된 항목 리스트

## 구현 위치
`src/search/history_search.py`
