# Deploy the AiTrainHK mod into the game (run while the game is CLOSED).
# Usage:
#   powershell -ExecutionPolicy Bypass -File deploy_mod.ps1
#   powershell -ExecutionPolicy Bypass -File deploy_mod.ps1 -Build   # rebuild before deploying
#
# NOTE: this file must stay ASCII-only and is kept in UTF-8 **with BOM**.
# Windows PowerShell 5.1 without a BOM reads non-ASCII bytes as CP1251, which used
# to break the parser with "Missing closing '}'" on the first block. The script text
# is English/ASCII now, but the BOM is kept as a safety net: if you edit the file with
# an editor that strips the BOM, put it back.

param(
    [switch]$Build
)

$ErrorActionPreference = "Stop"

# --- Locate the installed game (same approach as the csproj: Steam registry + default libraries) ---
function Find-GameDir {
    $steam = (Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam' -ErrorAction SilentlyContinue).InstallPath
    $libs = @()
    if ($steam) {
        $libs += $steam
        $vdf = Join-Path $steam 'steamapps\libraryfolders.vdf'
        if (Test-Path $vdf) {
            $libs += (Get-Content $vdf | Select-String '"path"' | ForEach-Object { ($_ -split '"')[3] -replace '\\\\', '\' })
        }
    }
    $libs += @('C:\Games\Steam', 'D:\Games\Steam', 'C:\Program Files (x86)\Steam', 'C:\Program Files\Steam')

    foreach ($lib in $libs) {
        if (-not $lib) { continue }
        $p = Join-Path $lib 'steamapps\common\Hollow Knight'
        if (Test-Path $p) { return $p }
    }
    return $null
}

$gameDir = Find-GameDir
if (-not $gameDir) {
    Write-Host "Hollow Knight was not found (Steam registry + default paths)." -ForegroundColor Red
    exit 1
}
Write-Host "Game: $gameDir"

$game = Get-Process -Name "hollow_knight" -ErrorAction SilentlyContinue
if ($game) {
    Write-Host "The game is running (PID $($game.Id)) - close Hollow Knight and deploy again." -ForegroundColor Yellow
    exit 1
}

if ($Build) {
    Write-Host "Building the mod (Release)..."
    dotnet build (Join-Path $PSScriptRoot "Mod\AiTrainHK\AiTrainHK.csproj") -c Release
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Build failed." -ForegroundColor Red
        exit 1
    }
}

$dll = Join-Path $PSScriptRoot "Mod\AiTrainHK\bin\Release\net472\AiTrainHK.dll"
if (-not (Test-Path $dll)) {
    Write-Host "DLL not found: $dll (run dotnet build -c Release first)" -ForegroundColor Red
    exit 1
}

$modsDir = Join-Path $gameDir "hollow_knight_Data\Managed\Mods\AiTrainHK"
if (-not (Test-Path $modsDir)) {
    New-Item -ItemType Directory -Path $modsDir -Force | Out-Null
}
Copy-Item $dll (Join-Path $modsDir "AiTrainHK.dll") -Force
Write-Host "Deploy finished: $modsDir\AiTrainHK.dll" -ForegroundColor Green
Write-Host "Launch the game and check ModLog - the mod version should be 1."
