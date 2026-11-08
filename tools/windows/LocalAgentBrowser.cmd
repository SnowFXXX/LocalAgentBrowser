@echo off
rem LocalAgentBrowser launcher for Windows: checks that LM Studio is answering, then runs the agent in this window.
rem Each run gets its own folder under Documents\LocalAgentBrowser runs, so nothing in the project folder is touched.
rem Used by the Start menu and desktop shortcuts (tools\windows\make_shortcuts.ps1).
title LocalAgentBrowser
set "APP=%~dp0..\.."
powershell -NoProfile -Command "try { Invoke-RestMethod -TimeoutSec 5 http://localhost:1234/v1/models | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo LM Studio is not answering at http://localhost:1234.
  echo Start LM Studio, load a model, and start its local server, then open LocalAgentBrowser again.
  echo.
  pause
  exit /b 1
)
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "STAMP=%%t"
set "RUN=%USERPROFILE%\Documents\LocalAgentBrowser runs\%STAMP%"
mkdir "%RUN%"
cd /d "%RUN%"
echo Reports for this run go to: %RUN%
echo.
set "PY=python"
if exist "%APP%\venv\Scripts\python.exe" set "PY=%APP%\venv\Scripts\python.exe"
if exist "%APP%\venv\Scripts\python.exe" (
  "%PY%" "%APP%\agent_browser.py"
) else (
  localagentbrowser
)
echo.
echo Finished. The report is research_report.md in %RUN%
rem The report opens as a page in the browser, in front; if that fails, the run folder opens instead
"%PY%" "%APP%\tools\windows\show_report.py" "%RUN%" || start "" "%RUN%"
pause
