@echo off
rem The project's 7-task regression suite, from the Start-menu shortcut "Run the test suite" (installed version).
title LocalAgentBrowser test suite
set "APP=%~dp0..\.."
if exist "%APP%\settings\model.cmd" call "%APP%\settings\model.cmd"
if not defined LMSTUDIO_MODEL set "LMSTUDIO_MODEL=qwen2.5-7b-instruct"
echo This runs the project's 7 test tasks one after the other with %LMSTUDIO_MODEL% in LM Studio,
echo up to 90 minutes each: on a CPU it takes several hours. Results go to:
echo   %APP%\regression_runs
echo Close this window to stop at any time; finished tasks keep their results.
echo.
pause
"%APP%\python\python.exe" "%APP%\tools\regression.py" --model %LMSTUDIO_MODEL% --minutes 90
echo.
start "" "%APP%\regression_runs"
pause
