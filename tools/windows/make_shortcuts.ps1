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

foreach ($folder in @($programs, $desktop)) {
    New-Shortcut $folder "LocalAgentBrowser" (Join-Path $here "LocalAgentBrowser.cmd") "" $repo `
        (Join-Path $here "localagentbrowser.ico") "Research the web with a local model; every claim cited"
    if ($Radio) {
        New-Shortcut $folder "Radio Lookout" (Join-Path $Radio ".venv\Scripts\pythonw.exe") "tray.py" $Radio `
            (Join-Path $Radio "radio-lookout.ico") "Radio Lookout in the taskbar"
    }
}
Write-Output "Shortcuts made in the Start menu and on the desktop."
