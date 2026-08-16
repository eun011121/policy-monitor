# legislative-tracker

## 역할
국회 의안정보시스템에서 소관 4개 상임위 계류 법안의 상태 변경(발의/상정/의결 등)을 추적한다.

## 수집 방법
1차: 열린국회정보 Open API (open.assembly.go.kr)
  - 환경변수 `ASSEMBLY_API_KEY` 필요
  - 설정 방법: `set ASSEMBLY_API_KEY=발급받은키` (Windows)
2차: 의안정보시스템 HTML 스크래핑 (API 키 없을 때 자동 대체)

## 소관 상임위 코드
- 산자중기위: 9770772
- 과방위: 9770771
- 농해수위: 9770770
- 정무위: 9770762

## API 키 발급
https://open.assembly.go.kr/portal/main.do → 회원가입 → API 신청

## 출력 형식
`{title, url, date, source='국회 의안정보시스템', committee, bill_id, status, proposer, type='legislation'}`
