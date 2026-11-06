@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
if not exist "%~dp0bank_merge.py" (
  echo Extract the whole ZIP before running start.cmd.
  if not "%BANK_NO_PAUSE%"=="1" pause
  exit /b 1
)
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
set "PYLAUNCHER_ALLOW_INSTALL="
set "PYLAUNCHER_ALWAYS_INSTALL="
set "PYLAUNCHER_DRYRUN="
set "BANK_SELECTED="
set "BANK_SELECTED_OPTION="
set "BANK_SELECTOR="
set "BANK_PROBE_DIR=%TEMP%\bank-python-%RANDOM%-%RANDOM%"
if exist "%BANK_PROBE_DIR%" (
  echo Unable to create a temporary Python check. Please try again.
  exit /b 1
)
md "%BANK_PROBE_DIR%" >nul 2>nul
if not exist "%BANK_PROBE_DIR%\" (
  echo Unable to write to the temporary folder.
  if not "%BANK_NO_PAUSE%"=="1" pause
  exit /b 1
)
set "BANK_PROBE_FILE=%BANK_PROBE_DIR%\probe.txt"

rem Keep the working fallback used by the older release ahead of PATH aliases.
if defined BANK_PYTHON (
  set "BANK_CANDIDATE=%BANK_PYTHON%"
  call :test_candidate
)
if defined BANK_SELECTED goto run_app
set "BANK_CANDIDATE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
call :test_candidate
if defined BANK_SELECTED goto run_app

rem A registered installation may not have been added to PATH.
for %%R in (HKCU HKLM) do for %%V in (64 32) do (
  for /f "tokens=1,2,*" %%A in ('reg.exe query "%%R\Software\Python" /s /v ExecutablePath /reg:%%V 2^>nul') do (
    if /i "%%B"=="REG_SZ" (
      set "BANK_CANDIDATE=%%C"
      call :test_candidate
    )
  )
)
if defined BANK_SELECTED goto run_app

for %%D in ("%LOCALAPPDATA%\Programs\Python\Python*" "%LOCALAPPDATA%\Python\pythoncore-*" "%ProgramFiles%\Python*" "%ProgramFiles(x86)%\Python*" "%SystemDrive%\Python*") do (
  for /d %%P in (%%D) do (
    set "BANK_CANDIDATE=%%~fP\python.exe"
    call :test_candidate
  )
)
if defined BANK_SELECTED goto run_app

rem Probe every result instead of trusting the first command found by WHERE.
for %%N in (python.exe python3.exe) do (
  for /f "delims=" %%P in ('where.exe %%N 2^>nul') do (
    set "BANK_CANDIDATE=%%P"
    call :test_candidate
  )
)
if defined BANK_SELECTED goto run_app
set "BANK_SELECTOR=-3"
for /f "delims=" %%P in ('where.exe py.exe 2^>nul') do (
  set "BANK_CANDIDATE=%%P"
  call :test_candidate
)
if defined BANK_SELECTED goto run_app

echo No usable Python 3.10 or newer with tkinter was found.
echo If Python is already installed, its command or window components may be unavailable.
echo Install or repair Python from python.org with Tcl/Tk selected, then try again.
set "BANK_EXIT=1"
goto finished

:test_candidate
if defined BANK_SELECTED exit /b 0
if not exist "%BANK_CANDIDATE%" exit /b 1
rem Zero-length Windows App Installer shortcuts are not Python interpreters.
for %%E in ("%BANK_CANDIDATE%") do if %%~zE EQU 0 exit /b 1
"%BANK_CANDIDATE%" %BANK_SELECTOR% -I -X utf8 -B -c "import sys; sys.exit(1) if sys.version_info < (3,10) else None; import tkinter; r=tkinter.Tk(); r.withdraw(); r.update_idletasks(); r.destroy(); print('BANK_PYTHON_READY')" >"%BANK_PROBE_FILE%" 2>nul
if errorlevel 1 exit /b 1
"%SystemRoot%\System32\findstr.exe" /x /c:"BANK_PYTHON_READY" "%BANK_PROBE_FILE%" >nul 2>nul
if errorlevel 1 exit /b 1
set "BANK_SELECTED=%BANK_CANDIDATE%"
set "BANK_SELECTED_OPTION=%BANK_SELECTOR%"
exit /b 0

:run_app
"%BANK_SELECTED%" %BANK_SELECTED_OPTION% -E -X utf8 -B "%~dp0bank_merge.py" %*
set "BANK_EXIT=%ERRORLEVEL%"
:finished
del /q "%BANK_PROBE_FILE%" >nul 2>nul
rd "%BANK_PROBE_DIR%" >nul 2>nul
if not "%BANK_EXIT%"=="0" if not "%BANK_NO_PAUSE%"=="1" pause
exit /b %BANK_EXIT%
