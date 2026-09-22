@echo off
REM Opens the texture pool viewer on the folder this file is in.
REM Keep poolview.py and pooldump.py next to it.
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw poolview.py %*
) else (
  python poolview.py %*
  if errorlevel 1 pause
)
