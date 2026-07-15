# A minimal, fast, LCARS-styled PowerShell prompt used inside the terminal
# panes of the LCARS TUI. It intentionally skips the user's normal profile
# (Starship/Oh-My-Posh, etc.) so panes stay compact and match the console
# theme instead of the user's everyday shell.
param(
    [string]$Label = "PWSH",
    [ConsoleColor]$Accent = [ConsoleColor]::Gray
)

function global:prompt {
    $leaf = Split-Path -Leaf -Path (Get-Location)
    if ([string]::IsNullOrEmpty($leaf)) { $leaf = (Get-Location).Path }

    Write-Host (" {0} " -f $Label.ToUpperInvariant()) -NoNewline -BackgroundColor $Accent -ForegroundColor Black
    Write-Host (" {0}" -f $leaf) -NoNewline -ForegroundColor $Accent
    return " > "
}

Clear-Host
Write-Host ("LCARS // {0} STATION ONLINE" -f $Label.ToUpperInvariant()) -ForegroundColor $Accent
