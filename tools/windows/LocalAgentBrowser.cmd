@echo off
rem LocalAgentBrowser launcher for Windows: checks that the model server is ready, then runs the agent in this window.
rem Each run gets its own folder under Documents\LocalAgentBrowser runs, so nothing in the program's folder is touched.
rem Used by the Start menu and desktop shortcuts: from the installer, or from a clone (tools\windows\make_shortcuts.ps1).
title LocalAgentBrowser
rem The program folder, as a plain full path (no "..\.." in messages)
for %%i in ("%~dp0..\..") do set "APP=%%~fi"
rem Choices made in setup: the model, and cloud models if they were turned on
if exist "%APP%\settings\model.cmd" call "%APP%\settings\model.cmd"
if exist "%APP%\settings\cloud.cmd" call "%APP%\settings\cloud.cmd"
if defined LAB_CLOUD (
  if not defined OPENROUTER_API_KEY (
    echo Cloud models are turned on, but the OPENROUTER_API_KEY environment variable is not set.
    echo Set it to your OpenRouter key in Windows ^(Edit environment variables for your account^), then open
    echo LocalAgentBrowser again. To use LM Studio instead, delete "%APP%\settings\cloud.cmd".
    echo.
    pause
    exit /b 1
  )
  set "LMSTUDIO_API_KEY=%OPENROUTER_API_KEY%"
  echo Using cloud models through OpenRouter ^(%LMSTUDIO_MODEL%^): the prompts and pages read go to OpenRouter.
) else (
  powershell -NoProfile -Command "try { Invoke-RestMethod -TimeoutSec 5 http://localhost:1234/v1/models | Out-Null; exit 0 } catch { exit 1 }"
  if errorlevel 1 (
    echo LM Studio is not answering at http://localhost:1234.
    echo Start LM Studio, load a model, and start its local server, then open LocalAgentBrowser again.
    echo.
    pause
    exit /b 1
  )
)
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "STAMP=%%t"
rem Windows' own Documents folder (it may be in OneDrive), the same one the installer's "Past reports" shortcut opens
for /f "delims=" %%d in ('powershell -NoProfile -Command "[Environment]::GetFolderPath('MyDocuments')"') do set "DOCS=%%d"
if not defined DOCS set "DOCS=%USERPROFILE%\Documents"
set "RUN=%DOCS%\LocalAgentBrowser runs\%STAMP%"
mkdir "%RUN%"
cd /d "%RUN%"
echo Reports for this run go to: %RUN%
echo.
rem The installed app has its own Python; a clone uses its venv; otherwise the pip-installed command
set "PY=python"
if exist "%APP%\venv\Scripts\python.exe" set "PY=%APP%\venv\Scripts\python.exe"
if exist "%APP%\python\python.exe" set "PY=%APP%\python\python.exe"
if exist "%APP%\agent_browser.py" (
  "%PY%" "%APP%\agent_browser.py"
) else (
  localagentbrowser
)
echo.
echo Finished. The report is research_report.md in %RUN%
rem The report opens as a page in the browser, in front; if that fails, the run folder opens instead
"%PY%" "%APP%\tools\windows\show_report.py" "%RUN%" || start "" "%RUN%"
pause
