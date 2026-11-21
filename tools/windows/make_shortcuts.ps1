# Creates Start menu and desktop shortcuts for LocalAgentBrowser (and, with -Radio, the Radio Lookout tray app).
# Usage: powershell -ExecutionPolicy Bypass -File tools\windows\make_shortcuts.ps1 [-Radio C:\path\to\experiments\radio]
param([string]$Radio = "")
$shell = New-Object -ComObject WScript.Shell
$programs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$desktop = [Environment]::GetFolderPath("Desktop")
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = (Resolve-Path (Join-Path $here "..\..")).Path

function New-Shortcut($folder, $name, $target, $arguments, $workdir, $icon, $description) {
    $link = $shell.CreateShortcut((Join-Path $folder "$name.lnk"))
    $link.TargetPath = $target
    $link.Arguments = $arguments
    $link.WorkingDirectory = $workdir
    if ($icon -and (Test-Path $icon)) { $link.IconLocation = $icon }
    $link.Description = $description
    $link.Save()
}

# The Windows UI runs without a console window (pythonw): the clone's venv if it has one, else the Python on PATH. It
# opens classic mode instead when that is the saved choice
$pythonw = Join-Path $repo "venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = "pythonw.exe" }

foreach ($folder in @($programs, $desktop)) {
    New-Shortcut $folder "LocalAgentBrowser" $pythonw "-m localagentbrowser.winui" $repo `
        (Join-Path $here "localagentbrowser.ico") "Research the web with a local model; every claim cited"
    if ($folder -eq $programs) {
        New-Shortcut $folder "LocalAgentBrowser (classic console)" (Join-Path $here "LocalAgentBrowser.cmd") "" $repo `
            (Join-Path $here "localagentbrowser.ico") "Research the web in a console window (classic mode)"
    }
    if ($Radio) {
        New-Shortcut $folder "Radio Lookout" (Join-Path $Radio ".venv\Scripts\pythonw.exe") "tray.py" $Radio `
            (Join-Path $Radio "radio-lookout.ico") "Radio Lookout in the taskbar"
    }
}
Write-Output "Shortcuts made in the Start menu and on the desktop."
