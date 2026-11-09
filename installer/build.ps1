# Builds the Windows installer: installer\build\LocalAgentBrowser-<version>-setup.exe
# Usage (from the repository): powershell -ExecutionPolicy Bypass -File installer\build.ps1
#
# The installed app carries its own Python (the official embeddable package from python.org), with the app's packages
# installed beside it, so users need no Python of their own. Playwright's Chromium is downloaded on the user's PC
# during setup, as every Playwright app does. Needs: Inno Setup 6 (ISCC.exe) and Python 3.13 (the py launcher).
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$build = Join-Path $PSScriptRoot "build"
$stage = Join-Path $build "app"
$pyVersion = "3.13.15"
$version = (Select-String -Path (Join-Path $repo "pyproject.toml") -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 is not installed (winget install JRSoftware.InnoSetup --scope user)" }

Write-Host "Building LocalAgentBrowser $version with Python $pyVersion"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $stage, (Join-Path $build "cache") | Out-Null

# 1. Python: the official embeddable package, cached between builds; its SHA-256 goes in the build record
$zip = Join-Path $build "cache\python-$pyVersion-embed-amd64.zip"
if (-not (Test-Path $zip)) {
    Invoke-WebRequest "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-embed-amd64.zip" -OutFile $zip
}
$pyHash = (Get-FileHash $zip).Hash
Expand-Archive $zip (Join-Path $stage "python")
# The embeddable Python reads its search path only from this file: add the packages folder and the app folder
# (agent_browser.py and the localagentbrowser package live one level up), and turn on site for .pth files
$pth = Get-ChildItem (Join-Path $stage "python") -Filter "python*._pth" | Select-Object -First 1
$zipName = (Get-ChildItem (Join-Path $stage "python") -Filter "python*.zip" | Select-Object -First 1).Name
Set-Content $pth.FullName -Encoding ascii -Value @($zipName, ".", "Lib\site-packages", "..", "import site")

# 2. The app's packages, the exact versions in requirements.txt, built for this Python
& py -3.13 -m pip install --quiet --disable-pip-version-check --no-warn-script-location --no-compile `
    --target (Join-Path $stage "python\Lib\site-packages") -r (Join-Path $repo "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
# No precompiled caches: Python rebuilds them on first use, and their long names can pass Windows' 260-character
# path limit in a deep install folder
Get-ChildItem (Join-Path $stage "python") -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# 3. The app: the repository's tracked files as they are in this working tree (no tests, no drafts)
Push-Location $repo
$files = git ls-files | Where-Object { $_ -notmatch '^(tests|docs|benchmarks|installer|\.github)/' -and $_ -notmatch '^\.' }
foreach ($f in $files) {
    $dest = Join-Path $stage $f
    New-Item -ItemType Directory -Force (Split-Path -Parent $dest) | Out-Null
    Copy-Item $f $dest
}
Pop-Location
Copy-Item (Join-Path $PSScriptRoot "run_suite.cmd") (Join-Path $stage "tools\windows\run_suite.cmd")

# 4. A record of what went in
$record = @("LocalAgentBrowser $version", "built $(Get-Date -Format 'yyyy-MM-dd HH:mm')", "commit $(git -C $repo rev-parse --short HEAD)",
            "python-$pyVersion-embed-amd64.zip sha256 $pyHash", "packages:") +
          (& py -3.13 -m pip list --path (Join-Path $stage "python\Lib\site-packages") --format freeze)
Set-Content (Join-Path $stage "BUILD.txt") -Encoding utf8 -Value $record

# 5. The setup file
& $iscc /Q "/DAppVersion=$version" "/DStage=$stage" "/O$build" (Join-Path $PSScriptRoot "LocalAgentBrowser.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
$setup = Join-Path $build "LocalAgentBrowser-$version-setup.exe"
Write-Host "Built $setup ($([math]::Round((Get-Item $setup).Length / 1MB, 1)) MB), SHA-256 $((Get-FileHash $setup).Hash)"
