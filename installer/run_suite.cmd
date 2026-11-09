@echo off
rem The project's 7-task regression suite, from the Start-menu shortcut "Run the test suite" (installed version).
title LocalAgentBrowser test suite
rem The program folder, as a plain full path (no "..\.." in messages)
for %%i in ("%~dp0..\..") do set "APP=%%~fi"
if exist "%APP%\settings\model.cmd" call "%APP%\settings\model.cmd"
if not defined LMSTUDIO_MODEL set "LMSTUDIO_MODEL=qwen2.5-7b-instruct"
rem Results go to Windows' own Documents folder (it may be in OneDrive), kept apart from the program, so they
rem survive an uninstall and don't keep the program folder from being removed
for /f "delims=" %%d in ('powershell -NoProfile -Command "[Environment]::GetFolderPath('MyDocuments')"') do set "DOCS=%%d"
if not defined DOCS set "DOCS=%USERPROFILE%\Documents"
set "LAB_RUNS_DIR=%DOCS%\LocalAgentBrowser test-suite runs"
echo This runs the project's 7 test tasks one after the other with %LMSTUDIO_MODEL% in LM Studio,
echo up to 90 minutes each: on a CPU it takes several hours. Results go to:
echo   %LAB_RUNS_DIR%
echo Close this window to stop at any time; finished tasks keep their results.
echo.
pause
"%APP%\python\python.exe" "%APP%\tools\regression.py" --model %LMSTUDIO_MODEL% --minutes 90
echo.
start "" "%LAB_RUNS_DIR%"
pause
