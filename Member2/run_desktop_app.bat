@echo off
setlocal
cd /d "%~dp0"
if not exist "desktop_app\main.py" (
  echo CAPRLM desktop files were not found in: %CD%
  echo Run this launcher from the Member2 project folder.
  pause
  exit /b 1
)
set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo Project interpreter not found at %PYTHON_EXE%
  echo Create and activate a project virtual environment, then install requirements.
  pause
  exit /b 1
)
"%PYTHON_EXE%" -c "import PySide6, sklearn, joblib" >nul 2>&1
if errorlevel 1 (
  echo Required desktop or model dependencies are missing from the project environment.
  echo Run: .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)
"%PYTHON_EXE%" -m desktop_app.main
if errorlevel 1 (
  echo The desktop application exited with an error. Review the message above.
  pause
  exit /b 1
)
endlocal
