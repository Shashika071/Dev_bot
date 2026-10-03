@echo off
cd /d "%~dp0"
echo Starting Deriv Touch Bot...
echo Tick database folder: %CD%\pgdata
echo Do NOT delete that folder. Do NOT use: docker compose down -v
docker compose up -d --build
echo.
echo UI:      http://localhost:5173
echo API:     http://localhost:8000/health
echo Train:   check tick count under Train Model (database total)
pause
