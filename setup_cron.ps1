# setup_cron.ps1 — Windows 작업 스케줄러에 매일 오전 9시 실행 등록
# 관리자 권한으로 실행 필요

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PythonExe  = (Get-Command python).Source
$RunScript  = Join-Path $ProjectDir "run.py"
$LogFile    = Join-Path $ProjectDir "logs\scheduler.log"

$Action  = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$RunScript`" run" `
    -WorkingDirectory $ProjectDir

$Trigger = New-ScheduledTaskTrigger -Daily -At "09:00"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName   "PolicyMonitorDailyRun" `
    -TaskPath   "\PolicyMonitor\" `
    -Action     $Action `
    -Trigger    $Trigger `
    -Settings   $Settings `
    -Description "소관분야 정책 모니터링 에이전트 — 매일 09:00 자동 실행" `
    -Force

Write-Host "작업 스케줄러 등록 완료: PolicyMonitor\PolicyMonitorDailyRun"
Write-Host "확인: 작업 스케줄러 → PolicyMonitor 폴더"
