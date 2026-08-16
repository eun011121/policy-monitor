# install.ps1 — 의존성 설치 및 초기 설정
param(
    [switch]$SkipVenv,
    [switch]$SetupCron
)

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ProjectDir

Write-Host "=== Policy Monitor 설치 ===" -ForegroundColor Cyan

# 1. 가상환경
if (-not $SkipVenv) {
    Write-Host "가상환경 생성 중..." -ForegroundColor Yellow
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    Write-Host "가상환경 활성화됨" -ForegroundColor Green
}

# 2. 의존성 설치
Write-Host "패키지 설치 중..." -ForegroundColor Yellow
pip install -r requirements.txt

# 3. 초기 디렉터리 생성
@("archive", "index", "logs", "feedback") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir $_) | Out-Null
}

# 4. 국회 API 키 안내
Write-Host ""
Write-Host "=== 국회 Open API 키 설정 (선택사항) ===" -ForegroundColor Cyan
Write-Host "API 키 없이도 동작하지만, 키가 있으면 입법 추적 정확도가 높아집니다."
Write-Host "발급: https://open.assembly.go.kr/portal/main.do"
Write-Host "키 설정 방법 (PowerShell):"
Write-Host '  $env:ASSEMBLY_API_KEY = "발급받은키"' -ForegroundColor Gray
Write-Host "  또는 시스템 환경변수에 ASSEMBLY_API_KEY 추가"
Write-Host ""

# 5. 크론 등록 (옵션)
if ($SetupCron) {
    Write-Host "작업 스케줄러 등록 중..." -ForegroundColor Yellow
    & "$ProjectDir\setup_cron.ps1"
}

Write-Host ""
Write-Host "=== 설치 완료 ===" -ForegroundColor Green
Write-Host "실행 방법:"
Write-Host "  python run.py              # 오늘 수집 실행" -ForegroundColor White
Write-Host "  python run.py search 키워드  # 아카이브 검색" -ForegroundColor White
Write-Host "  .\install.ps1 -SetupCron   # 매일 9시 자동 실행 등록" -ForegroundColor White
