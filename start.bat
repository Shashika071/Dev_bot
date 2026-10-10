@echo off
cd /d "%~dp0"
echo Starting Deriv Touch Bot...
echo Tick database folder: %CD%\pgdata
echo Do NOT delete that folder. Do NOT use: docker compose down -v
docker compose up -d --build
echo.
echo UI:      http://127.0.0.1:5173
echo API:     http://127.0.0.1:8000/health
echo Digit Matches is a sidebar tab. Guide: docs\DIGITMATCH.md
echo Default mode is OBSERVE. It does not buy until you change it.
pause
