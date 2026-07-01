# 修复 HTCondor 提交账户密码密文的读取权限。
# 运行方式：管理员 PowerShell 中执行本脚本。
# 这个脚本只改 ACL，不改密码，不改 HTCondor 配置。

param(
    [string]$ProjectRoot = 'D:\local_web_module_system',
    [string]$BackendUser = ''
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Info($Text) {
    Write-Host "[INFO] $Text" -ForegroundColor Cyan
}

function Write-Ok($Text) {
    Write-Host "[OK] $Text" -ForegroundColor Green
}

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    throw '请用管理员 PowerShell 运行这个脚本。'
}

if ([string]::IsNullOrWhiteSpace($BackendUser)) {
    $BackendUser = $identity.Name
}

$runtimeDir = Join-Path $ProjectRoot 'backend\runtime\htcondor'
$secretPath = Join-Path $runtimeDir 'submit_account_secret.bin'

if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
    throw "找不到密码密文文件：$secretPath。请先运行 HTCondor 一键安装。"
}

Write-Info "项目目录：$ProjectRoot"
Write-Info "后端用户：$BackendUser"
Write-Info "运行目录：$runtimeDir"
Write-Info "密文文件：$secretPath"

# 后端需要读取密文文件，才能用 LocalWebCondor 身份提交 condor_submit。
& icacls.exe $secretPath /grant "${BackendUser}:R" /C | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "给 $BackendUser 授权读取密文文件失败。"
}

# 后端还需要在 runtime\htcondor 下创建 jobs、日志和临时脚本。
& icacls.exe $runtimeDir /grant "${BackendUser}:(OI)(CI)M" /T /C | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "给 $BackendUser 授权读写 runtime 目录失败。"
}

# 保留系统账户和管理员权限，避免后续一键安装修复失败。
& icacls.exe $runtimeDir /grant 'SYSTEM:(OI)(CI)F' /T /C | Out-Host
& icacls.exe $runtimeDir /grant 'Administrators:(OI)(CI)F' /T /C | Out-Host

Write-Ok 'ACL 修复完成。请关闭当前后端窗口，重新运行 start_system.bat，然后在 HTCondor 页面再次提交自检任务。'
