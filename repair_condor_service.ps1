# repair_condor_service.ps1
# 作用：在 Condor 服务无法启动时，先移除 local_web 自动写入的集群配置块，清理残留 condor 进程，再启动 Condor 服务。
# 使用方式：用“管理员 PowerShell”执行：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\repair_condor_service.ps1

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

Write-Host "========== LocalWeb Condor Service Repair ==========" -ForegroundColor Cyan

$cfg = 'C:\Condor\condor_config.local'
$logDir = 'D:\local_web_module_system\backend\logs\htcondor'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Write-Host "[1/5] 当前 Condor 服务状态" -ForegroundColor Yellow
sc.exe queryex Condor

Write-Host "`n[2/5] 备份并移除 LocalWeb 自动写入的配置块" -ForegroundColor Yellow
if (Test-Path -LiteralPath $cfg -PathType Leaf) {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backup = Join-Path $logDir "condor_config.local.before_repair_$stamp.txt"
    Copy-Item -LiteralPath $cfg -Destination $backup -Force
    Write-Host "[OK] 已备份：$backup"

    $text = Get-Content -LiteralPath $cfg -Raw -ErrorAction SilentlyContinue
    if ($null -eq $text) { $text = '' }
    $pattern = '(?s)\r?\n?# === LOCAL_WEB_HTCONDOR_POOL_START ===.*?# === LOCAL_WEB_HTCONDOR_POOL_END ===\r?\n?'
    $newText = [regex]::Replace($text, $pattern, "`r`n").TrimEnd() + "`r`n"
    $ascii = [Text.Encoding]::ASCII
    [IO.File]::WriteAllText($cfg, $newText, $ascii)
    Write-Host "[OK] 已移除 LOCAL_WEB_HTCONDOR_POOL 配置块：$cfg"
} else {
    Write-Host "[WARN] 找不到配置文件：$cfg" -ForegroundColor Yellow
}

Write-Host "`n[3/5] 清理残留 Condor 进程" -ForegroundColor Yellow
$procs = Get-Process condor* -ErrorAction SilentlyContinue
if ($procs) {
    foreach ($p in $procs) {
        try {
            Write-Host "Killing $($p.ProcessName) PID=$($p.Id)"
            Stop-Process -Id $p.Id -Force -ErrorAction Stop
        } catch {
            Write-Host "[WARN] 无法结束 $($p.ProcessName) PID=$($p.Id)：$($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    Start-Sleep -Seconds 3
} else {
    Write-Host "[OK] 没有发现残留 condor* 进程"
}

Write-Host "`n[4/5] 启动 Condor 服务" -ForegroundColor Yellow
try {
    Start-Service -Name Condor -ErrorAction Stop
} catch {
    Write-Host "[WARN] Start-Service 失败：$($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "尝试使用 sc.exe start Condor ..."
    sc.exe start Condor
}

Start-Sleep -Seconds 8

Write-Host "`n[5/5] 最终状态与最近日志" -ForegroundColor Yellow
sc.exe queryex Condor

$bin = 'C:\Condor\bin'
if (Test-Path -LiteralPath (Join-Path $bin 'condor_status.exe')) {
    Write-Host "`n[condor_status]" -ForegroundColor Cyan
    & (Join-Path $bin 'condor_status.exe') 2>&1 | Select-Object -First 40
}

foreach ($log in @('C:\Condor\log\MasterLog','C:\Condor\log\StartLog','C:\Condor\log\SchedLog')) {
    if (Test-Path -LiteralPath $log -PathType Leaf) {
        Write-Host "`n==== Tail: $log ====" -ForegroundColor Cyan
        Get-Content -LiteralPath $log -Tail 80 -ErrorAction SilentlyContinue
    }
}

Write-Host "`n========== Repair Finished ==========" -ForegroundColor Cyan
Write-Host "如果 STATE 显示 RUNNING，再重新执行 D:\local_web_module_system\start_system.bat" -ForegroundColor Green
