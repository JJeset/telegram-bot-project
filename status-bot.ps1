# Показывает, запущен ли бот сейчас, и где искать логи, если он упал.

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $scriptDir "bot.pid"
$errLog  = Join-Path $scriptDir "logs\bot.err.log"

if (-not (Test-Path $pidFile)) {
    Write-Host "Бот не запущен (файл bot.pid отсутствует)." -ForegroundColor Yellow
    exit 0
}

$raw = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
$targetPid = 0
$isValid = $false
if ($raw) {
    $isValid = [int]::TryParse($raw.ToString().Trim(), [ref]$targetPid) -and $targetPid -gt 0
}

if (-not $isValid) {
    Write-Host "Файл bot.pid повреждён или пуст — статус бота неизвестен." -ForegroundColor Yellow
    exit 0
}

$proc = Get-Process -Id $targetPid -ErrorAction SilentlyContinue

if ($proc) {
    Write-Host "Бот работает. PID $targetPid, запущен: $($proc.StartTime)" -ForegroundColor Green
} else {
    Write-Host "PID $targetPid из bot.pid не активен — бот, похоже, упал." -ForegroundColor Red
    if (Test-Path $errLog) {
        Write-Host "Последние строки ошибок ($errLog):"
        Get-Content $errLog -Tail 15
    }
}
