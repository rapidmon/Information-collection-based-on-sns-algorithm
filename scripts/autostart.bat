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

REM 2) Server - skip if port 8000 already listening
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if errorlevel 1 (
  echo [%date% %time%] autostart: starting serve >> logs\autostart.log
  .venv\Scripts\python.exe main.py serve >> logs\autostart_serve.log 2>&1
) else (
  echo [%date% %time%] autostart: port 8000 in use, skipping serve >> logs\autostart.log
)


