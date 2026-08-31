# Останавливает бота, запущенного через start-bot.ps1 (использует PID из bot.pid).

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $scriptDir "bot.pid"

if (-not (Test-Path $pidFile)) {
    Write-Host "Файл bot.pid не найден — бот не был запущен через start_bot.bat, либо уже остановлен." -ForegroundColor Yellow
    exit 0
}

$raw = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
$targetPid = 0
$isValid = $false
if ($raw) {
    $isValid = [int]::TryParse($raw.ToString().Trim(), [ref]$targetPid) -and $targetPid -gt 0
}

if (-not $isValid) {
    Write-Host "Файл bot.pid повреждён или пуст. Удаляю его — если бот всё ещё работает, найдите процесс python в Диспетчере задач вручную." -ForegroundColor Yellow
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    exit 0
}

$proc = Get-Process -Id $targetPid -ErrorAction SilentlyContinue

if (-not $proc) {
    Write-Host "Процесс с PID $targetPid не найден — похоже, бот уже остановлен." -ForegroundColor Yellow
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    exit 0
}

try {
    Stop-Process -Id $targetPid -Force -ErrorAction Stop
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    Write-Host "Бот остановлен (PID $targetPid)." -ForegroundColor Green
} catch {
    Write-Host "Не удалось остановить процесс PID ${targetPid}: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
