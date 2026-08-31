# Запускает бота в фоне (без открытого окна консоли) и запоминает его PID в bot.pid.
# Вывод бота пишется в logs\bot.log (обычные сообщения) и logs\bot.err.log (ошибки).

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$pidFile = Join-Path $scriptDir "bot.pid"
$logDir  = Join-Path $scriptDir "logs"
$outLog  = Join-Path $logDir "bot.log"
$errLog  = Join-Path $logDir "bot.err.log"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

function Get-ValidPid {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    $raw = Get-Content $Path -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $raw) { return $null }
    $parsed = 0
    if ([int]::TryParse($raw.ToString().Trim(), [ref]$parsed) -and $parsed -gt 0) {
        return $parsed
    }
    return $null
}

# Проверяем, не запущен ли бот уже (по валидному PID из bot.pid)
$existingPid = Get-ValidPid -Path $pidFile
if ($existingPid) {
    $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($existingProc) {
        Write-Host "Бот уже запущен (PID $existingPid). Сначала остановите его через stop_bot.bat." -ForegroundColor Yellow
        exit 1
    }
}

try {
    $process = Start-Process -FilePath "python" `
        -ArgumentList "telegram_video_bot.py" `
        -WorkingDirectory $scriptDir `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -WindowStyle Hidden `
        -PassThru `
        -ErrorAction Stop
} catch {
    Write-Host "Не удалось запустить python. Проверьте, что Python установлен и доступен в PATH." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

Start-Sleep -Seconds 2

if ($process.HasExited) {
    Write-Host "Бот сразу завершился (код выхода $($process.ExitCode)). Смотрите $errLog" -ForegroundColor Red
    exit 1
}

$process.Id | Out-File -FilePath $pidFile -Encoding ascii -NoNewline

Write-Host "Бот запущен, PID $($process.Id)." -ForegroundColor Green
Write-Host "Логи: $outLog (и $errLog при ошибках)"
