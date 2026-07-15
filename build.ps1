<#
.SYNOPSIS
    Builds a portable, standalone lcars.exe (no Python install required on
    the target machine) using PyInstaller.

.DESCRIPTION
    Produces a onedir build in dist\lcars\ -- a folder you can zip up and
    copy to any Windows machine. Onedir (not onefile) is used on purpose:
    onefile re-extracts a temp copy of pywinpty's native DLLs on every
    launch, which is slower to start and can trip antivirus heuristics.

.PARAMETER Wheelhouse
    Path to a local wheelhouse containing a `pyinstaller` wheel, used only
    if PyInstaller isn't already installed in .venv (this repo has no PyPI
    index configured, per requirements.txt install instructions).

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Wheelhouse C:\wheelhouse
#>
param(
    [string]$Wheelhouse
)

$ErrorActionPreference = "Stop"
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "No .venv found at $venvPython -- run the Setup steps in README.md first."
}

$pyinstallerExe = Join-Path $PSScriptRoot ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $pyinstallerExe)) {
    Write-Host "PyInstaller not found in .venv -- installing..."
    if ($Wheelhouse) {
        & $venvPython -m pip install --no-index --find-links $Wheelhouse pyinstaller
    } else {
        & $venvPython -m pip install pyinstaller
    }
}

Push-Location $PSScriptRoot
try {
    & $pyinstallerExe lcars.spec --noconfirm
} finally {
    Pop-Location
}

Write-Host "`nPortable build ready: dist\lcars\lcars.exe"
Write-Host "Copy the whole dist\lcars\ folder to any Windows machine and run lcars.exe from there."
