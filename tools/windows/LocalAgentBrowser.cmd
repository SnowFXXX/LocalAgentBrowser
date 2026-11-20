@echo off
rem LocalAgentBrowser launcher for Windows: checks that the model server is ready, then runs the agent in this window.
rem Each run gets its own folder under Documents\LocalAgentBrowser runs, so nothing in the program's folder is touched.
rem Used by the Start menu and desktop shortcuts: from the installer, or from a clone (tools\windows\make_shortcuts.ps1).
rem The window's title names the edition: Clean Room's build leaves out the region below, so its title keeps "Clean Room"
set "LAB_TITLE=LocalAgentBrowser Clean Room"
rem cloud:start
set "LAB_TITLE=LocalAgentBrowser"
rem cloud:end
title %LAB_TITLE%
rem The program folder, as a plain full path (no "..\.." in messages)
for %%i in ("%~dp0..\..") do set "APP=%%~fi"
rem The model chosen in setup
if exist "%APP%\settings\model.cmd" call "%APP%\settings\model.cmd"
rem cloud:start
rem Cloud models only from the "LocalAgentBrowser (cloud)" shortcut, which passes "cloud"; the plain shortcut always
rem uses the model in LM Studio, so nothing leaves the PC unless that shortcut was the one opened
set "LAB_CLOUD="
if /i "%~1"=="cloud" (
  if not exist "%APP%\settings\cloud.cmd" (
    echo Cloud models were not turned on in setup. To use them, run setup again and tick "Use cloud models";
    echo to use LM Studio, open the LocalAgentBrowser shortcut instead.
    echo.
    pause
    exit /b 1
  )
  call "%APP%\settings\cloud.cmd"
)
if defined LAB_CLOUD (
  title LocalAgentBrowser ^(cloud^)
  if not defined OPENROUTER_API_KEY (
    echo Cloud models are turned on, but the OPENROUTER_API_KEY environment variable is not set.
    echo Set it to your OpenRouter key in Windows ^(Edit environment variables for your account^), then open
    echo LocalAgentBrowser ^(cloud^) again. To use LM Studio instead, open the LocalAgentBrowser shortcut.
    echo.
    pause
    exit /b 1
  )
  set "LMSTUDIO_API_KEY=%OPENROUTER_API_KEY%"
  echo Using cloud models through OpenRouter ^(%LMSTUDIO_MODEL%^): the prompts and pages read go to OpenRouter.
  goto server_ready
)
rem cloud:end
powershell -NoProfile -Command "try { Invoke-RestMethod -TimeoutSec 5 http://localhost:1234/v1/models | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo LM Studio is not answering at http://localhost:1234.
  echo Start LM Studio, load a model, and start its local server, then open LocalAgentBrowser again.
  echo.
  pause
  exit /b 1
)
:server_ready
rem Windows' own Documents folder (it may be in OneDrive), the same one the installer's "Past reports" shortcut opens
for /f "delims=" %%d in ('powershell -NoProfile -Command "[Environment]::GetFolderPath('MyDocuments')"') do set "DOCS=%%d"
if not defined DOCS set "DOCS=%USERPROFILE%\Documents"
rem The installed app has its own Python; a clone uses its venv; otherwise the pip-installed command
set "PY=python"
if exist "%APP%\venv\Scripts\python.exe" set "PY=%APP%\venv\Scripts\python.exe"
if exist "%APP%\python\python.exe" set "PY=%APP%\python\python.exe"
rem One run after another in this window, each in its own folder, until an empty objective: the console stays open
rem on the left with the last report on the right. LAB_RUN_COUNT tells the program which run it is (only the first
rem starts with the console full size)
set "LAB_RUN_COUNT=0"
:next
set /a LAB_RUN_COUNT+=1
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "STAMP=%%t"
set "RUN=%DOCS%\LocalAgentBrowser runs\%STAMP%"
rem Two runs started within the same second (one that ended at once) must not share a folder
if exist "%RUN%" set "RUN=%RUN%_%LAB_RUN_COUNT%"
mkdir "%RUN%"
cd /d "%RUN%"
echo.
if %LAB_RUN_COUNT% GTR 1 echo Type another objective, or press Enter on an empty line to close LocalAgentBrowser.
echo Reports for this run go to: %RUN%
echo.
if exist "%APP%\agent_browser.py" (
  "%PY%" "%APP%\agent_browser.py"
) else (
  localagentbrowser
)
rem No report means no objective was given: the empty run folder goes, and so does the window
if not exist "%RUN%\research_report.md" (
  cd /d "%DOCS%"
  rmdir "%RUN%" 2>nul
  exit /b 0
)
echo.
echo Finished. The report is research_report.md in %RUN%
rem The report opens as a page in the browser, in front; if that fails, the run folder opens instead
"%PY%" "%APP%\tools\windows\show_report.py" "%RUN%" || start "" "%RUN%"
goto next
