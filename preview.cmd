@echo off
cd /d "%~dp0"
python scripts/sync_garmin.py %*
pause
