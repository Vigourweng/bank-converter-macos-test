@echo off
setlocal
chcp 65001 >nul
if not exist "%~dp0bank_merge.py" (
  echo Extract the whole ZIP before running start.cmd.
  pause
  exit /b 1
)
where py >nul 2>nul
if not errorlevel 1 goto python_launcher
where python >nul 2>nul
if not errorlevel 1 goto python_command
echo Install Python 3.10 or newer with tkinter from python.org, then try again.
pause
exit /b 1
:python_launcher
py -3 -X utf8 -B "%~dp0bank_merge.py" %*
goto finished
:python_command
python -X utf8 -B "%~dp0bank_merge.py" %*
:finished
if errorlevel 1 pause
endlocal
