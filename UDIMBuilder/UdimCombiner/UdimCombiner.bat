@echo off
pushd "%~dp0"
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found. Install Python and make sure it is available on PATH.
    pause
    popd
    exit /b 1
)

python -c "import P4; from PySide6 import QtWidgets; from importlib.metadata import version; import sys; p4=tuple(map(int,version('P4Python').split('.')[:2])); qt=tuple(map(int,version('PySide6').split('.')[:2])); sys.exit(0 if p4 >= (2023,1) and (6,7) <= qt < (7,0) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Installing missing or outdated UDIM Builder requirements...
    call "%~dp0InstallRequirements.bat" --no-pause
    if errorlevel 1 (
        echo [ERROR] Requirements installation failed. See the pip output above.
        pause
        popd
        exit /b 1
    )
    python -c "import P4; from PySide6 import QtWidgets" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Requirements installed, but this Python cannot import them.
        pause
        popd
        exit /b 1
    )
)

python UdimCombiner.py --ui
if errorlevel 1 pause
popd
