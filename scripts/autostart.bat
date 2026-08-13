@echo off
REM Auto-recovery after reboot: launched hidden by sns_briefing_autostart.vbs in the Startup folder
cd /d "C:\Users\DONGA\Desktop\Information-collection-based-on-sns-algorithm"

REM Wait for network/services to settle after boot
ping -n 21 127.0.0.1 >nul

REM 1) Chrome debug mode - skip if port 9222 already listening
netstat -ano | findstr ":9222" | findstr "LISTENING" >nul
if errorlevel 1 (
  start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome_temp"
  ping -n 11 127.0.0.1 >nul
)

REM 2) Cloudflare tunnel - skip if already running.
REM    Without this the public origin (api.cnvjb.uk) stays down after a reboot:
REM    the GitHub Pages dashboard gets 530 and Slack app_mention events never arrive.
tasklist /fi "imagename eq cloudflared-new.exe" | findstr /i "cloudflared-new.exe" >nul
if errorlevel 1 (
  echo [%date% %time%] autostart: starting cloudflared >> logs\autostart.log
  start "" /b "%USERPROFILE%\.cloudflared\cloudflared-new.exe" tunnel run 5cdbe678-a5cb-41e9-bbc7-fd0159a58650 >> logs\cloudflared.log 2>&1
  REM Stamp the watchdog's grace marker so a 5-min tick during boot does not start a second one.
  echo autostart> logs\.watchdog_tunnel
  ping -n 6 127.0.0.1 >nul
) else (
  echo [%date% %time%] autostart: cloudflared already running, skipping >> logs\autostart.log
)

REM 3) Server - skip if port 8000 already listening
REM    NOTE: this blocks until serve exits - keep it last.
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if errorlevel 1 (
  echo [%date% %time%] autostart: starting serve >> logs\autostart.log
  REM Same grace marker as above - serve takes a while to bind :8000.
  echo autostart> logs\.watchdog_serve
  .venv\Scripts\python.exe main.py serve >> logs\autostart_serve.log 2>&1
) else (
  echo [%date% %time%] autostart: port 8000 in use, skipping serve >> logs\autostart.log
)


