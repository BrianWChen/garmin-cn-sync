@echo off
cd /d "%~dp0"
python sync_garmin.py --apply --only wellness %*
pause
