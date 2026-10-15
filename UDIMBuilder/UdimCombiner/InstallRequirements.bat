@echo off
pushd "%~dp0"
python -m pip install -r requirements.txt
set "UDIM_REQUIREMENTS_EXIT=%errorlevel%"
popd
if /I not "%~1"=="--no-pause" pause
exit /b %UDIM_REQUIREMENTS_EXIT%
