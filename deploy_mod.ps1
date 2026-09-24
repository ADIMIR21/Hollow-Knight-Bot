# Деплой мода HK_AI_Mod в игру (запускать при ЗАКРЫТОЙ игре).
# Использование:
#   powershell -ExecutionPolicy Bypass -File deploy_mod.ps1
#   powershell -ExecutionPolicy Bypass -File deploy_mod.ps1 -Build   # пересобрать перед деплоем

param(
    [switch]$Build
)

$ErrorActionPreference = "Stop"

# --- Поиск установленной игры (как в csproj: реестр Steam + стандартные библиотеки) ---
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
    Write-Host "Hollow Knight не найден (реестр Steam + стандартные пути)." -ForegroundColor Red
    exit 1
}
Write-Host "Игра: $gameDir"

$game = Get-Process -Name "hollow_knight" -ErrorAction SilentlyContinue
if ($game) {
    Write-Host "Игра запущена (PID $($game.Id)) — закрой Hollow Knight и повтори деплой." -ForegroundColor Yellow
    exit 1
}

if ($Build) {
    Write-Host "Сборка мода (Release)..."
    dotnet build (Join-Path $PSScriptRoot "Mod\HK_AI_Mod\HK_AI_Mod.csproj") -c Release
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Сборка не удалась." -ForegroundColor Red
        exit 1
    }
}

$dll = Join-Path $PSScriptRoot "Mod\HK_AI_Mod\bin\Release\net472\HK_AI_Mod.dll"
if (-not (Test-Path $dll)) {
    Write-Host "DLL не найдена: $dll (сначала dotnet build -c Release)" -ForegroundColor Red
    exit 1
}

$modsDir = Join-Path $gameDir "hollow_knight_Data\Managed\Mods\HK_AI_Mod"
if (-not (Test-Path $modsDir)) {
    New-Item -ItemType Directory -Path $modsDir -Force | Out-Null
}
Copy-Item $dll (Join-Path $modsDir "HK_AI_Mod.dll") -Force
Write-Host "Деплой выполнен: $modsDir\HK_AI_Mod.dll" -ForegroundColor Green
Write-Host "Запусти игру и проверь ModLog — версия мода должна быть 1.2."
