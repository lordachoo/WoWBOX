@echo off
rem Start WoWBox from the source checkout. Extra arguments are passed through (e.g. --no-attach).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m wowbox %*
