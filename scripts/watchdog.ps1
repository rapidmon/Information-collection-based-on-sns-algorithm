<#
  파이프라인 워치독 — 작업 스케줄러가 5분마다 호출한다.
  cloudflared 터널과 serve가 죽어 있으면 다시 띄운다.

  배경: 2026-08-12 터널이 재부팅 없이 혼자 죽어(uptime 8일) 대시보드와 슬랙 멘션이
  며칠 방치됐다. autostart.bat은 로그인 시점에만 돌기 때문에 이 실패 모드를 못 잡는다.
  워치독은 "혼자 죽은 뒤 아무도 안 살리는" 그 구멍만 메운다.

  로그인 세션 제약은 그대로다 — 로그아웃 상태에선 이 작업도 안 돈다(수집이 사용자
  Chrome CDP에 붙는 구조라 어차피 데스크톱 세션이 필요하다).
#>

$ErrorActionPreference = 'Stop'

$Root      = 'C:\Users\DONGA\Desktop\Information-collection-based-on-sns-algorithm'
$LogDir    = Join-Path $Root 'logs'
$LogPath   = Join-Path $LogDir 'watchdog.log'
$TunnelExe = Join-Path $env:USERPROFILE '.cloudflared\cloudflared-new.exe'
$TunnelId  = '5cdbe678-a5cb-41e9-bbc7-fd0159a58650'

# 기동 직후 재시도를 막는 유예. 부팅/로그인 때 autostart.bat과 겹쳐도 중복 기동되지 않게,
# 그리고 serve가 뜨는 데 걸리는 시간 동안 워치독이 한 번 더 띄우지 않게 한다.
$GraceMinutes = 5

function Write-WatchdogLog([string]$Message) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $LogPath -Value $line -Encoding utf8
}

function Test-RecentlyStarted([string]$Marker) {
    $path = Join-Path $LogDir ".watchdog_$Marker"
    if (-not (Test-Path $path)) { return $false }
    return (((Get-Date) - (Get-Item $path).LastWriteTime).TotalMinutes -lt $GraceMinutes)
}

function Set-StartMarker([string]$Marker) {
    Set-Content -Path (Join-Path $LogDir ".watchdog_$Marker") -Value (Get-Date -Format 'o') -Encoding utf8
}

# 재기동분 로그는 타임스탬프 파일로 남긴다 — 덮어쓰면 죽은 원인이 든 직전 로그가 사라진다.
function New-RunLogPath([string]$Prefix, [string]$Suffix) {
    Join-Path $LogDir ('{0}_{1}_{2}.log' -f $Prefix, (Get-Date -Format 'yyyyMMdd_HHmmss'), $Suffix)
}

try {
    # ── 1) cloudflared 터널 ──
    # 프로세스명이 cloudflared 가 아니라 cloudflared-new 다(바이너리 파일명 그대로).
    $tunnelUp = [bool](Get-Process -Name 'cloudflared-new' -ErrorAction SilentlyContinue)
    if (-not $tunnelUp) {
        if (Test-RecentlyStarted 'tunnel') {
            Write-WatchdogLog 'cloudflared 없음 — 방금 기동했으므로 대기(유예 중)'
        } elseif (-not (Test-Path $TunnelExe)) {
            Write-WatchdogLog "cloudflared 기동 불가 — 실행 파일 없음: $TunnelExe"
        } else {
            $err = New-RunLogPath 'cloudflared' 'err'
            Set-StartMarker 'tunnel'
            Start-Process -FilePath $TunnelExe `
                -ArgumentList 'tunnel', 'run', $TunnelId `
                -WindowStyle Hidden `
                -RedirectStandardOutput (New-RunLogPath 'cloudflared' 'out') `
                -RedirectStandardError  $err
            Write-WatchdogLog "cloudflared 죽어 있어 재기동 → $err"
        }
    }

    # ── 2) serve ──
    # serve 는 부모(.venv python) + 자식(8000 리슨) 쌍으로 뜬다. 둘 중 하나만 봐도
    # 놓칠 수 있어 커맨드라인과 포트를 함께 본다.
    $serveProcs = @(
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like '*main.py*serve*' }
    )
    $portUp  = [bool](Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
    $serveUp = ($serveProcs.Count -gt 0) -or $portUp

    if (-not $serveUp) {
        if (Test-RecentlyStarted 'serve') {
            Write-WatchdogLog 'serve 없음 — 방금 기동했으므로 대기(유예 중)'
        } else {
            $err = New-RunLogPath 'serve' 'err'
            Set-StartMarker 'serve'
            Start-Process -FilePath (Join-Path $Root '.venv\Scripts\python.exe') `
                -ArgumentList 'main.py', 'serve' `
                -WorkingDirectory $Root `
                -WindowStyle Hidden `
                -RedirectStandardOutput (New-RunLogPath 'serve' 'out') `
                -RedirectStandardError  $err
            Write-WatchdogLog "serve 죽어 있어 재기동 → $err"
        }
    }
}
catch {
    # 워치독 자신의 실패가 조용히 묻히면 워치독이 없는 것과 같다.
    try { Write-WatchdogLog "워치독 오류: $($_.Exception.Message)" } catch { }
}
