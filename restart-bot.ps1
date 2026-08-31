# Перезапускает бота: останавливает (если запущен) и запускает заново.
#
# stop-bot.ps1 и start-bot.ps1 запускаются как ОТДЕЛЬНЫЕ процессы powershell.exe
# (через Start-Process), а не вызовом "& script.ps1" внутри этого же процесса.
# Это важно: если бы они вызывались напрямую, "exit" внутри них завершил бы
# весь restart-bot.ps1 целиком, и start-bot.ps1 мог бы просто не запуститься.

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Останавливаю бота..."
Start-Process -FilePath "powershell" -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $scriptDir "stop-bot.ps1")
) -Wait -NoNewWindow

Start-Sleep -Seconds 1

Write-Host "Запускаю бота..."
Start-Process -FilePath "powershell" -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $scriptDir "start-bot.ps1")
) -Wait -NoNewWindow
