param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [string]$InstallDir = "C:\Condor",

    [string]$SubmitAccount = "LocalWebCondor",

    # 后端实际启动用户。这里用 SID 处理，避免中文用户名导致 ACL 解析失败。
    [string]$BackendUserName = "",

    [string]$BackendUserSid = "",

    [switch]$NoAutoElevate,

    [switch]$ForceReinstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Windows PowerShell 5.1 does not always load the assembly that contains
# ProtectedData automatically. Load it explicitly before any DPAPI call.
function Initialize-DpapiSupport {
    try {
        Add-Type -AssemblyName System.Security -ErrorAction Stop
    }
    catch {
        throw (
            "无法加载 Windows DPAPI 支持程序集 System.Security：" +
            $_.Exception.Message
        )
    }

    $protectedDataType = "System.Security.Cryptography.ProtectedData" -as [type]
    $scopeType = "System.Security.Cryptography.DataProtectionScope" -as [type]

    if (-not $protectedDataType -or -not $scopeType) {
        throw "当前 PowerShell 无法使用 Windows DPAPI（ProtectedData）。"
    }
}

Initialize-DpapiSupport

$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$BackendDir = Join-Path $ProjectRoot "backend"
$RuntimeDir = Join-Path $BackendDir "runtime\htcondor"
$LogDir = Join-Path $BackendDir "logs\htcondor"

$MsiPath = Join-Path $ProjectRoot "third_party\htcondor\condor-Windows-x64.msi"
$ManifestPath = Join-Path $ProjectRoot "third_party\htcondor\manifest.json"

$ServiceName = "Condor"
$ResultPath = Join-Path $RuntimeDir "install_result.json"
$StatePath = Join-Path $RuntimeDir "install_state.json"
$SecretPath = Join-Path $RuntimeDir "submit_account_secret.bin"

$ComputerName = $env:COMPUTERNAME
$UidDomain = $ComputerName.ToLowerInvariant()
$SubmitIdentity = "$SubmitAccount@$UidDomain"
$WindowsSubmitAccount = "$ComputerName\$SubmitAccount"

# 这里记录启动后端的普通用户。
# 密码密文仍然是 LocalMachine DPAPI 加密，只是允许后端进程读取密文字节，
# 不再让用户手工执行 icacls / ACL 修复脚本。
if ([string]::IsNullOrWhiteSpace($BackendUserName)) {
    $BackendUserName = [Security.Principal.WindowsIdentity]::GetCurrent().Name
}
if ([string]::IsNullOrWhiteSpace($BackendUserSid)) {
    try {
        $BackendUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    }
    catch {
        $BackendUserSid = ""
    }
}
$BackendAclIdentity = if (-not [string]::IsNullOrWhiteSpace($BackendUserSid)) {
    "*$BackendUserSid"
}
else {
    $BackendUserName
}

$CompletedSteps = New-Object System.Collections.Generic.List[string]
$CurrentStage = "starting"
$InstallLog = ""
$ExtractLog = ""
$SmokeResultPath = Join-Path $RuntimeDir "smoke_test_result.json"

function Write-Step {
    param([string]$Text)

    Write-Host ""
    Write-Host "============================================================"
    Write-Host "[STEP] $Text"
    Write-Host "============================================================"
}

function Write-Info {
    param([string]$Text)
    Write-Host "[INFO] $Text"
}

function Write-Ok {
    param([string]$Text)
    Write-Host "[OK] $Text" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Text)
    Write-Host "[WARN] $Text" -ForegroundColor Yellow
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        $Value
    )

    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }

    $Value |
        ConvertTo-Json -Depth 15 |
        Set-Content -LiteralPath $Path -Encoding UTF8
}

function Add-CompletedStep {
    param([string]$Name)

    if (-not $CompletedSteps.Contains($Name)) {
        [void]$CompletedSteps.Add($Name)
    }
}

function Save-State {
    param(
        [string]$Stage,
        [string]$Message,
        [bool]$Success = $false
    )

    $script:CurrentStage = $Stage

    $state = [ordered]@{
        success = $Success
        stage = $Stage
        message = $Message
        machine = $ComputerName
        uid_domain = $UidDomain
        submit_account = $SubmitAccount
        submit_identity = $SubmitIdentity
        completed_steps = @($CompletedSteps)
        updated_at = [DateTime]::UtcNow.ToString("o")
    }

    Write-JsonFile -Path $StatePath -Value $state
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)

    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

function Quote-ProcessArgument {
    param([string]$Value)

    if ($null -eq $Value) {
        return '""'
    }

    return '"' + ($Value -replace '"', '\"') + '"'
}

function Add-MachinePathEntry {
    param([string]$Entry)

    $machinePath = [Environment]::GetEnvironmentVariable(
        "Path",
        [EnvironmentVariableTarget]::Machine
    )

    $items = @(
        $machinePath -split ";" |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
    )

    if ($items -notcontains $Entry) {
        $newPath = if ([string]::IsNullOrWhiteSpace($machinePath)) {
            $Entry
        }
        else {
            "$machinePath;$Entry"
        }

        [Environment]::SetEnvironmentVariable(
            "Path",
            $newPath,
            [EnvironmentVariableTarget]::Machine
        )
    }

    if (@($env:PATH -split ";") -notcontains $Entry) {
        $env:PATH = "$Entry;$env:PATH"
    }
}

function Assert-Bundle {
    if (-not (Test-Path -LiteralPath $MsiPath -PathType Leaf)) {
        throw "找不到内置 HTCondor MSI：$MsiPath"
    }

    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "找不到 HTCondor 清单：$ManifestPath"
    }

    $manifest = Get-Content -LiteralPath $ManifestPath -Raw |
        ConvertFrom-Json

    $expectedVersion = [string]$manifest.product_version
    $expectedHash = ([string]$manifest.sha256).Trim().ToLowerInvariant()

    if ([string]::IsNullOrWhiteSpace($expectedVersion)) {
        throw "manifest.json 中没有 product_version。"
    }

    if ([string]::IsNullOrWhiteSpace($expectedHash)) {
        throw "manifest.json 中没有 sha256。"
    }

    $actualHash = (
        Get-FileHash -LiteralPath $MsiPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()

    if ($actualHash -ne $expectedHash) {
        throw (
            "HTCondor MSI SHA-256 校验失败。" +
            " expected=$expectedHash actual=$actualHash"
        )
    }

    return [ordered]@{
        product_version = $expectedVersion
        expected_sha256 = $expectedHash
        actual_sha256 = $actualHash
    }
}

function Get-CondorRuntime {
    $binDir = Join-Path $InstallDir "bin"
    $versionExe = Join-Path $binDir "condor_version.exe"
    $configValExe = Join-Path $binDir "condor_config_val.exe"
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

    $versionOutput = ""
    $versionOk = $false
    $releaseDir = ""

    if (Test-Path -LiteralPath $versionExe -PathType Leaf) {
        try {
            $versionOutput = (
                & $versionExe 2>&1 | Out-String
            ).Trim()
            $versionOk = ($LASTEXITCODE -eq 0)
        }
        catch {}
    }

    if (Test-Path -LiteralPath $configValExe -PathType Leaf) {
        try {
            $releaseDir = (
                & $configValExe RELEASE_DIR 2>&1 | Out-String
            ).Trim()
        }
        catch {}
    }

    return [ordered]@{
        installed = [bool](
            $service -and
            $versionOk -and
            $releaseDir
        )
        service_exists = [bool]$service
        service_status = if ($service) {
            [string]$service.Status
        }
        else {
            "NotInstalled"
        }
        version_output = $versionOutput
        release_dir = $releaseDir
        bin_dir = $binDir
        version_exe = $versionExe
        config_val_exe = $configValExe
    }
}

function Stop-And-RemoveCondorService {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

    if (-not $service) {
        return
    }

    if ($service.Status -ne "Stopped") {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue

        try {
            $service.WaitForStatus(
                [System.ServiceProcess.ServiceControllerStatus]::Stopped,
                [TimeSpan]::FromSeconds(30)
            )
        }
        catch {}
    }

    & sc.exe delete $ServiceName | Out-Null

    $deadline = (Get-Date).AddSeconds(30)
    while (
        (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) -and
        (Get-Date) -lt $deadline
    ) {
        Start-Sleep -Seconds 1
    }

    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        throw "旧的 Condor 服务无法删除。"
    }
}

function Write-CondorBaseConfig {
    param(
        [bool]$KnownUsersOnly
    )

    $configPath = Join-Path $InstallDir "condor_config"
    $localConfigPath = Join-Path $InstallDir "condor_config.local"

    $configText = @"
# Generated by local_web_module_system.

RELEASE_DIR = $InstallDir
LOCAL_DIR = $InstallDir

BIN = `$(RELEASE_DIR)\bin
SBIN = `$(RELEASE_DIR)\bin
LIB = `$(RELEASE_DIR)\lib

LOG = `$(LOCAL_DIR)\log
SPOOL = `$(LOCAL_DIR)\spool
EXECUTE = `$(LOCAL_DIR)\execute

LOCAL_CONFIG_FILE = $localConfigPath
REQUIRE_LOCAL_CONFIG_FILE = TRUE
"@

    $knownUsersValue = if ($KnownUsersOnly) {
        "TRUE"
    }
    else {
        "FALSE"
    }

    $localConfigText = @"
# Generated by local_web_module_system.
# Installation validation uses a loopback-only personal pool.
# The web cluster manager will later replace this role configuration.

use ROLE: Personal

CONDOR_HOST = 127.0.0.1
COLLECTOR_HOST = `$(CONDOR_HOST)
NETWORK_INTERFACE = 127.0.0.1

UID_DOMAIN = $UidDomain
FILESYSTEM_DOMAIN = $UidDomain

SEC_DEFAULT_AUTHENTICATION = REQUIRED
SEC_DEFAULT_AUTHENTICATION_METHODS = NTSSPI
SEC_CLIENT_AUTHENTICATION_METHODS = NTSSPI

SEC_WRITE_AUTHENTICATION = REQUIRED
SEC_WRITE_AUTHENTICATION_METHODS = NTSSPI

SEC_ADMINISTRATOR_AUTHENTICATION = REQUIRED
SEC_ADMINISTRATOR_AUTHENTICATION_METHODS = NTSSPI

ALLOW_READ = *
ALLOW_WRITE = *
ALLOW_ADMINISTRATOR = *

QUEUE_SUPER_USERS = SYSTEM, condor
ALLOW_SUBMIT_FROM_KNOWN_USERS_ONLY = $knownUsersValue

START = TRUE
SUSPEND = FALSE
PREEMPT = FALSE
KILL = FALSE
"@

    Set-Content -LiteralPath $configPath -Value $configText -Encoding ASCII
    Set-Content -LiteralPath $localConfigPath -Value $localConfigText -Encoding ASCII

    $registryPath = "HKLM:\SOFTWARE\Condor"
    New-Item -Path $registryPath -Force | Out-Null

    New-ItemProperty `
        -Path $registryPath `
        -Name "CONDOR_CONFIG" `
        -Value $configPath `
        -PropertyType String `
        -Force |
        Out-Null

    New-ItemProperty `
        -Path $registryPath `
        -Name "RELEASE_DIR" `
        -Value $InstallDir `
        -PropertyType String `
        -Force |
        Out-Null
}

function Install-CondorFromAdministrativeImage {
    param(
        [string]$ExpectedVersion
    )

    Write-Step "安装 HTCondor 文件和 Windows 服务"
    Save-State `
        -Stage "installing_files" `
        -Message "正在使用官方 MSI 管理映像提取 HTCondor。"

    Stop-And-RemoveCondorService

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $stagingDir = Join-Path $RuntimeDir "admin_image_$timestamp"
    $script:ExtractLog = Join-Path $LogDir "htcondor_admin_extract_$timestamp.log"

    if (Test-Path -LiteralPath $stagingDir) {
        Remove-Item -LiteralPath $stagingDir -Recurse -Force
    }

    New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null

    $extractArguments = @(
        "/a"
        ('"{0}"' -f $MsiPath)
        "/qn"
        "/norestart"
        ('TARGETDIR="{0}"' -f $stagingDir)
        "/L*v"
        ('"{0}"' -f $ExtractLog)
    )

    $extractProcess = Start-Process `
        -FilePath "msiexec.exe" `
        -ArgumentList $extractArguments `
        -Wait `
        -PassThru

    if (@(0, 1641, 3010) -notcontains [int]$extractProcess.ExitCode) {
        throw (
            "HTCondor 管理映像提取失败，退出码 " +
            "$($extractProcess.ExitCode)。日志：$ExtractLog"
        )
    }

    $masterCandidates = @(
        Get-ChildItem `
            -LiteralPath $stagingDir `
            -Filter "condor_master.exe" `
            -Recurse `
            -File `
            -ErrorAction SilentlyContinue |
        Where-Object {
            $_.DirectoryName -match '[\\/]bin$'
        } |
        Sort-Object { $_.FullName.Length }
    )

    if ($masterCandidates.Count -eq 0) {
        throw "管理映像中没有找到 condor_master.exe。"
    }

    $sourceBin = Split-Path -Parent $masterCandidates[0].FullName
    $sourceRoot = Split-Path -Parent $sourceBin

    if (Test-Path -LiteralPath $InstallDir) {
        $backupDir = (
            "${InstallDir}_backup_" +
            (Get-Date -Format "yyyyMMdd_HHmmss")
        )

        Move-Item `
            -LiteralPath $InstallDir `
            -Destination $backupDir `
            -Force
    }

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

    Get-ChildItem -LiteralPath $sourceRoot -Force |
        ForEach-Object {
            Copy-Item `
                -LiteralPath $_.FullName `
                -Destination $InstallDir `
                -Recurse `
                -Force
        }

    foreach ($name in @(
        "log",
        "spool",
        "execute",
        "local",
        "tokens.d",
        "tokens.sk"
    )) {
        New-Item `
            -ItemType Directory `
            -Force `
            -Path (Join-Path $InstallDir $name) |
            Out-Null
    }

    Write-CondorBaseConfig -KnownUsersOnly $false

    $binDir = Join-Path $InstallDir "bin"
    $masterPath = Join-Path $binDir "condor_master.exe"
    $versionPath = Join-Path $binDir "condor_version.exe"

    foreach ($requiredFile in @($masterPath, $versionPath)) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "缺少 HTCondor 文件：$requiredFile"
        }
    }

    Add-MachinePathEntry -Entry $binDir

    New-Service `
        -Name $ServiceName `
        -BinaryPathName ('"{0}"' -f $masterPath) `
        -DisplayName "Condor" `
        -StartupType Automatic |
        Out-Null

    & sc.exe description `
        $ServiceName `
        "HTCondor master service for local_web_module_system" |
        Out-Null

    Get-NetFirewallRule `
        -DisplayName "local_web_module_system HTCondor*" `
        -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue

    New-NetFirewallRule `
        -DisplayName "local_web_module_system HTCondor master" `
        -Direction Inbound `
        -Action Allow `
        -Program $masterPath `
        -Profile Private `
        -RemoteAddress LocalSubnet |
        Out-Null

    Start-Service -Name $ServiceName

    (Get-Service -Name $ServiceName).WaitForStatus(
        [System.ServiceProcess.ServiceControllerStatus]::Running,
        [TimeSpan]::FromSeconds(45)
    )

    $versionOutput = (
        & $versionPath 2>&1 | Out-String
    ).Trim()

    if (
        $ExpectedVersion -and
        $versionOutput -notmatch [regex]::Escape($ExpectedVersion)
    ) {
        throw (
            "安装版本与内置版本不一致。" +
            " expected=$ExpectedVersion output=$versionOutput"
        )
    }

    Add-CompletedStep "files_installed"
    Add-CompletedStep "service_created"
    Add-CompletedStep "service_running"

    Write-Ok "HTCondor 文件、配置和服务已安装。"
}

function Restart-CondorService {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

    if (-not $service) {
        throw "找不到 Condor Windows 服务。"
    }

    if ($service.Status -eq "Running") {
        Restart-Service -Name $ServiceName
    }
    else {
        Start-Service -Name $ServiceName
    }

    (Get-Service -Name $ServiceName).WaitForStatus(
        [System.ServiceProcess.ServiceControllerStatus]::Running,
        [TimeSpan]::FromSeconds(45)
    )

    Start-Sleep -Seconds 6
}

function Wait-ForCondorReady {
    $binDir = Join-Path $InstallDir "bin"
    $statusExe = Join-Path $binDir "condor_status.exe"
    $qExe = Join-Path $binDir "condor_q.exe"

    $deadline = (Get-Date).AddSeconds(60)
    $statusOk = $false
    $queueOk = $false

    while ((Get-Date) -lt $deadline) {
        try {
            & $statusExe -master 2>$null | Out-Null
            $statusOk = ($LASTEXITCODE -eq 0)

            & $qExe 2>$null | Out-Null
            $queueOk = ($LASTEXITCODE -eq 0)

            if ($statusOk -and $queueOk) {
                break
            }
        }
        catch {}

        Start-Sleep -Seconds 2
    }

    if (-not ($statusOk -and $queueOk)) {
        throw "Condor 服务已启动，但 collector 或 schedd 尚未就绪。"
    }
}

function New-StrongPassword {
    param([int]$Length = 40)

    if ($Length -lt 16) {
        $Length = 16
    }

    $upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    $lower = "abcdefghijkmnopqrstuvwxyz"
    $digits = "23456789"
    $special = "!#%+_-"
    $all = $upper + $lower + $digits + $special

    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()

    function Get-RandomCharacter {
        param(
            [string]$Characters,
            $Generator
        )

        $bytes = New-Object byte[] 4
        $Generator.GetBytes($bytes)
        $number = [BitConverter]::ToUInt32($bytes, 0)
        return $Characters[$number % $Characters.Length]
    }

    $characters = New-Object System.Collections.Generic.List[char]
    [void]$characters.Add((Get-RandomCharacter $upper $rng))
    [void]$characters.Add((Get-RandomCharacter $lower $rng))
    [void]$characters.Add((Get-RandomCharacter $digits $rng))
    [void]$characters.Add((Get-RandomCharacter $special $rng))

    while ($characters.Count -lt $Length) {
        [void]$characters.Add((Get-RandomCharacter $all $rng))
    }

    for ($index = $characters.Count - 1; $index -gt 0; $index--) {
        $bytes = New-Object byte[] 4
        $rng.GetBytes($bytes)
        $swapIndex = [BitConverter]::ToUInt32($bytes, 0) % ($index + 1)

        $temp = $characters[$index]
        $characters[$index] = $characters[$swapIndex]
        $characters[$swapIndex] = $temp
    }

    $rng.Dispose()
    return -join $characters
}

function Protect-LocalMachineSecret {
    param(
        [string]$PlainText,
        [string]$Path
    )

    $entropy = [Text.Encoding]::UTF8.GetBytes(
        "local_web_module_system.htcondor.submit_account.v1"
    )
    $bytes = [Text.Encoding]::UTF8.GetBytes($PlainText)

    $protectedBytes = [System.Security.Cryptography.ProtectedData]::Protect(
        $bytes,
        $entropy,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )

    [IO.File]::WriteAllBytes($Path, $protectedBytes)
}

function Unprotect-LocalMachineSecret {
    param([string]$Path)

    $entropy = [Text.Encoding]::UTF8.GetBytes(
        "local_web_module_system.htcondor.submit_account.v1"
    )
    $protectedBytes = [IO.File]::ReadAllBytes($Path)

    $bytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $protectedBytes,
        $entropy,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )

    return [Text.Encoding]::UTF8.GetString($bytes)
}

function New-FileReadRule {
    param([string]$IdentityText)

    if ([string]::IsNullOrWhiteSpace($IdentityText)) {
        return $null
    }

    try {
        if ($IdentityText.StartsWith("*S-")) {
            $sid = New-Object System.Security.Principal.SecurityIdentifier($IdentityText.Substring(1))
            return [System.Security.AccessControl.FileSystemAccessRule]::new(
                $sid,
                [System.Security.AccessControl.FileSystemRights]::Read,
                [System.Security.AccessControl.AccessControlType]::Allow
            )
        }

        return [System.Security.AccessControl.FileSystemAccessRule]::new(
            $IdentityText,
            [System.Security.AccessControl.FileSystemRights]::Read,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
    }
    catch {
        Write-Warn "添加密文读取权限失败：$IdentityText，$($_.Exception.Message)"
        return $null
    }
}

function Set-SecretFileAcl {
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)

    $rules = @(
        ([System.Security.AccessControl.FileSystemAccessRule]::new(
            "NT AUTHORITY\SYSTEM",
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.AccessControlType]::Allow
        )),
        ([System.Security.AccessControl.FileSystemAccessRule]::new(
            "BUILTIN\Administrators",
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.AccessControlType]::Allow
        )),
        ([System.Security.AccessControl.FileSystemAccessRule]::new(
            $WindowsSubmitAccount,
            [System.Security.AccessControl.FileSystemRights]::Read,
            [System.Security.AccessControl.AccessControlType]::Allow
        )),
        (New-FileReadRule -IdentityText $BackendAclIdentity)
    )

    foreach ($rule in $rules) {
        if ($null -ne $rule) {
            [void]$acl.AddAccessRule($rule)
        }
    }

    Set-Acl -LiteralPath $SecretPath -AclObject $acl
}

function Ensure-SubmitAccount {
    Write-Step "创建或修复 HTCondor 专用提交账户"
    Save-State `
        -Stage "creating_submit_account" `
        -Message "正在创建或修复专用提交账户。"

    $password = ""

    if (Test-Path -LiteralPath $SecretPath -PathType Leaf) {
        try {
            $password = Unprotect-LocalMachineSecret -Path $SecretPath
        }
        catch {
            Write-Warn "已有密码密文无法解密，将重新生成专用账户密码。"
        }
    }

    if ([string]::IsNullOrWhiteSpace($password)) {
        $password = New-StrongPassword
        Protect-LocalMachineSecret -PlainText $password -Path $SecretPath
    }

    $securePassword = ConvertTo-SecureString $password -AsPlainText -Force
    $localUser = Get-LocalUser -Name $SubmitAccount -ErrorAction SilentlyContinue

    if (-not $localUser) {
        New-LocalUser `
            -Name $SubmitAccount `
            -Password $securePassword `
            -FullName "Local Web Condor Submitter" `
            -Description "local_web_module_system 的 HTCondor 专用提交账户" `
            -PasswordNeverExpires |
            Out-Null
    }
    else {
        Set-LocalUser `
            -Name $SubmitAccount `
            -Password $securePassword `
            -PasswordNeverExpires $true

        Enable-LocalUser -Name $SubmitAccount
    }

    Set-SecretFileAcl

    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    & icacls.exe $ProjectRoot `
        /grant "${WindowsSubmitAccount}:(OI)(CI)RX" `
        /C |
        Out-Null

    & icacls.exe $RuntimeDir `
        /grant "${WindowsSubmitAccount}:(OI)(CI)M" `
        /T `
        /C |
        Out-Null

    & icacls.exe $LogDir `
        /grant "${WindowsSubmitAccount}:(OI)(CI)M" `
        /T `
        /C |
        Out-Null

    if (-not [string]::IsNullOrWhiteSpace($BackendAclIdentity)) {
        & icacls.exe $RuntimeDir `
            /grant "${BackendAclIdentity}:(OI)(CI)M" `
            /T `
            /C |
            Out-Null

        & icacls.exe $LogDir `
            /grant "${BackendAclIdentity}:(OI)(CI)M" `
            /T `
            /C |
            Out-Null
    }

    Add-CompletedStep "submit_account_created"
    Add-CompletedStep "submit_account_secret_protected"

    Write-Ok "专用提交账户已准备完成。"
    return $password
}

function Invoke-SystemPowerShell {
    param(
        [string]$ScriptPath,
        [string]$TaskName,
        [int]$TimeoutSeconds = 120
    )

    Unregister-ScheduledTask `
        -TaskName $TaskName `
        -Confirm:$false `
        -ErrorAction SilentlyContinue

    $powerShellExe = (
        "$env:SystemRoot\System32\" +
        "WindowsPowerShell\v1.0\powershell.exe"
    )

    $action = New-ScheduledTaskAction `
        -Execute $powerShellExe `
        -Argument (
            '-NoProfile -NonInteractive ' +
            '-ExecutionPolicy Bypass ' +
            '-File "' + $ScriptPath + '"'
        )

    $principal = New-ScheduledTaskPrincipal `
        -UserId "SYSTEM" `
        -LogonType ServiceAccount `
        -RunLevel Highest

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Seconds $TimeoutSeconds)

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Principal $principal `
        -Settings $settings `
        -Force |
        Out-Null

    try {
        # A scheduled task can finish before the first polling cycle.  The old
        # implementation only waited while State was already Running, so it
        # could unregister a task that was still in Ready state and had not
        # started yet.  Track LastRunTime as well as State to avoid that race.
        $beforeInfo = Get-ScheduledTaskInfo -TaskName $TaskName
        $beforeRunTime = $beforeInfo.LastRunTime
        $started = $false

        Start-ScheduledTask -TaskName $TaskName

        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 500

            $task = Get-ScheduledTask -TaskName $TaskName
            $info = Get-ScheduledTaskInfo -TaskName $TaskName

            if (
                $task.State -eq "Running" -or
                $info.LastRunTime -gt $beforeRunTime
            ) {
                $started = $true
            }

            if ($started -and $task.State -ne "Running") {
                return [int]$info.LastTaskResult
            }
        }

        $task = Get-ScheduledTask -TaskName $TaskName
        $info = Get-ScheduledTaskInfo -TaskName $TaskName

        if ($task.State -eq "Running") {
            Stop-ScheduledTask `
                -TaskName $TaskName `
                -ErrorAction SilentlyContinue
        }

        if (-not $started) {
            throw (
                "SYSTEM 初始化任务在超时时间内没有开始执行。" +
                " State=$($task.State)" +
                " LastTaskResult=$($info.LastTaskResult)"
            )
        }

        throw (
            "SYSTEM 初始化任务执行超时。" +
            " State=$($task.State)" +
            " LastTaskResult=$($info.LastTaskResult)"
        )
    }
    finally {
        Unregister-ScheduledTask `
            -TaskName $TaskName `
            -Confirm:$false `
            -ErrorAction SilentlyContinue
    }
}

function Ensure-CondorUserRecord {
    Write-Step "登记 HTCondor 提交用户"
    Save-State `
        -Stage "creating_user_record" `
        -Message "正在使用 SYSTEM 创建并修正 HTCondor User ClassAd。"

    $scriptPath = Join-Path $RuntimeDir "setup_user_record_as_system.ps1"
    $resultPath = Join-Path $RuntimeDir "user_record_result.json"

    # Use a literal template and replace only explicit placeholders. This avoids
    # nested expandable here-string parsing problems on Windows PowerShell 5.1.
    $scriptTemplate = @'
$ErrorActionPreference = "Stop"

$condorBin = __CONDOR_BIN__
$identity = __IDENTITY__
$domain = __DOMAIN__
$resultPath = __RESULT_PATH__
$qusersExe = Join-Path $condorBin "condor_qusers.exe"

function Save-Result {
    param($Data)

    $Data |
        ConvertTo-Json -Depth 10 |
        Set-Content -LiteralPath $resultPath -Encoding UTF8
}

try {
    $queryOutput = (& $qusersExe -user $identity -long 2>&1 | Out-String).Trim()

    $ownerName = ($identity -split "@", 2)[0]

    # HTCondor stores the canonical queue identity in User, while Windows
    # account mapping is split across OsUser and NTDomain.  On Windows,
    # condor_qusers may print string values with or without quotation marks.
    $userPattern = 'User\s*=\s*"?' + [regex]::Escape($identity) + '"?'
    $osUserPattern = 'OsUser\s*=\s*"?' + [regex]::Escape($ownerName) + '"?'
    $domainPattern = 'NTDomain\s*=\s*"?' + [regex]::Escape($domain) + '"?'

    if ($queryOutput -notmatch $userPattern) {
        $addOutput = (& $qusersExe -add $identity 2>&1 | Out-String).Trim()

        if ($LASTEXITCODE -ne 0) {
            throw "condor_qusers -add failed: $addOutput"
        }
    }

    $osUserEdit = 'OsUser="' + $ownerName + '"'
    $domainEdit = 'NTDomain="' + $domain + '"'
    $editOutput = (& $qusersExe -user $identity -edit $osUserEdit $domainEdit 2>&1 | Out-String).Trim()

    if ($LASTEXITCODE -ne 0) {
        throw "condor_qusers -edit failed: $editOutput"
    }

    $finalOutput = (& $qusersExe -user $identity -long 2>&1 | Out-String).Trim()

    # Some HTCondor Windows builds do not print NTDomain in the User ClassAd,
    # even after condor_qusers -edit succeeds.  The important fields for this
    # system are Enabled, User and OsUser.  NTDomain is useful, but should not
    # block startup by itself.
    $hasEnabled = $finalOutput -match 'Enabled\s*=\s*(1|true)'
    $hasUser = $finalOutput -match $userPattern
    $hasOsUser = $finalOutput -match $osUserPattern
    $hasDomain = $finalOutput -match $domainPattern

    if (-not ($hasEnabled -and $hasUser -and $hasOsUser)) {
        throw "User ClassAd validation failed: $finalOutput"
    }

    $warnText = ""
    if (-not $hasDomain) {
        $warnText = "NTDomain was not printed by condor_qusers, but User and OsUser are valid."
    }

    Save-Result -Data @{
        success = $true
        identity = $identity
        raw = $finalOutput
        warning = $warnText
        completed_at = [DateTime]::UtcNow.ToString("o")
    }

    exit 0
}
catch {
    Save-Result -Data @{
        success = $false
        identity = $identity
        error = $_.Exception.Message
        completed_at = [DateTime]::UtcNow.ToString("o")
    }

    exit 1
}
'@

    $condorBinValue = Join-Path $InstallDir "bin"
    $condorBinLiteral = "'" + ($condorBinValue -replace "'", "''") + "'"
    $identityLiteral = "'" + ($SubmitIdentity -replace "'", "''") + "'"
    $domainLiteral = "'" + ($UidDomain -replace "'", "''") + "'"
    $resultPathLiteral = "'" + ($resultPath -replace "'", "''") + "'"

    $scriptText = $scriptTemplate.Replace("__CONDOR_BIN__", $condorBinLiteral)
    $scriptText = $scriptText.Replace("__IDENTITY__", $identityLiteral)
    $scriptText = $scriptText.Replace("__DOMAIN__", $domainLiteral)
    $scriptText = $scriptText.Replace("__RESULT_PATH__", $resultPathLiteral)

    Remove-Item `
        -LiteralPath $resultPath `
        -Force `
        -ErrorAction SilentlyContinue

    Set-Content `
        -LiteralPath $scriptPath `
        -Value $scriptText `
        -Encoding UTF8

    $helperTokens = $null
    $helperErrors = $null

    [System.Management.Automation.Language.Parser]::ParseFile(
        $scriptPath,
        [ref]$helperTokens,
        [ref]$helperErrors
    ) | Out-Null

    if ($helperErrors.Count -gt 0) {
        $messages = @(
            $helperErrors |
                ForEach-Object {
                    "line $($_.Extent.StartLineNumber): $($_.Message)"
                }
        ) -join "; "

        throw "SYSTEM 用户记录辅助脚本语法错误：$messages"
    }

    $taskExitCode = Invoke-SystemPowerShell `
        -ScriptPath $scriptPath `
        -TaskName "LocalWeb-HTCondor-UserRecord" `
        -TimeoutSeconds 120

    if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) {
        throw (
            "SYSTEM 用户记录初始化没有生成结果文件。" +
            " TaskResult=$taskExitCode" +
            " HelperScript=$scriptPath"
        )
    }

    $result = Get-Content -LiteralPath $resultPath -Raw |
        ConvertFrom-Json

    if ($taskExitCode -ne 0 -or -not $result.success) {
        throw "HTCondor 用户记录初始化失败：$($result.error)"
    }

    Add-CompletedStep "user_record_created"
    Write-Ok "HTCondor User ClassAd 已创建并校验。"
}

function Invoke-AsSubmitAccount {
    param(
        [string]$ScriptPath,
        [string]$Password,
        [int]$TimeoutSeconds = 180
    )

    $securePassword = ConvertTo-SecureString $Password -AsPlainText -Force
    $credential = [System.Management.Automation.PSCredential]::new(
        $WindowsSubmitAccount,
        $securePassword
    )

    $arguments = @(
        "-NoProfile"
        "-NonInteractive"
        "-ExecutionPolicy"
        "Bypass"
        "-File"
        (Quote-ProcessArgument $ScriptPath)
    )

    $process = Start-Process `
        -FilePath "powershell.exe" `
        -Credential $credential `
        -ArgumentList $arguments `
        -WorkingDirectory $RuntimeDir `
        -Wait `
        -PassThru

    return [int]$process.ExitCode
}

function Ensure-SubmitCredential {
    param([string]$Password)

    Write-Step "保存 HTCondor 提交凭据"
    Save-State `
        -Stage "storing_credential" `
        -Message "正在为专用提交账户保存 HTCondor 凭据。"

    $scriptPath = Join-Path $RuntimeDir "store_submit_credential.ps1"
    $resultPath = Join-Path $RuntimeDir "credential_result.json"

    $scriptTemplate = @'
$ErrorActionPreference = "Stop"

try {
    Add-Type -AssemblyName System.Security -ErrorAction Stop
}
catch {
    throw ("无法加载 Windows DPAPI 支持程序集 System.Security：" + $_.Exception.Message)
}

$secretPath = __SECRET_PATH__
$resultPath = __RESULT_PATH__
$condorBin = __CONDOR_BIN__
$storeCredExe = Join-Path $condorBin "condor_store_cred.exe"

function Save-Result {
    param($Data)

    $Data |
        ConvertTo-Json -Depth 10 |
        Set-Content -LiteralPath $resultPath -Encoding UTF8
}

try {
    $entropy = [Text.Encoding]::UTF8.GetBytes(
        "local_web_module_system.htcondor.submit_account.v1"
    )
    $protectedBytes = [IO.File]::ReadAllBytes($secretPath)

    $bytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $protectedBytes,
        $entropy,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )

    $password = [Text.Encoding]::UTF8.GetString($bytes)
    $addOutput = (& $storeCredExe add -p $password 2>&1 | Out-String).Trim()

    if ($LASTEXITCODE -ne 0) {
        throw "condor_store_cred add failed: $addOutput"
    }

    $queryOutput = (& $storeCredExe query 2>&1 | Out-String).Trim()

    if (
        $LASTEXITCODE -ne 0 -or
        $queryOutput -notmatch "stored and is valid"
    ) {
        throw "condor_store_cred query failed: $queryOutput"
    }

    $password = $null
    [Array]::Clear($bytes, 0, $bytes.Length)

    Save-Result -Data @{
        success = $true
        account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        query = $queryOutput
        completed_at = [DateTime]::UtcNow.ToString("o")
    }

    exit 0
}
catch {
    Save-Result -Data @{
        success = $false
        error = $_.Exception.Message
        completed_at = [DateTime]::UtcNow.ToString("o")
    }

    exit 1
}
'@

    $secretPathLiteral = "'" + ($SecretPath -replace "'", "''") + "'"
    $resultPathLiteral = "'" + ($resultPath -replace "'", "''") + "'"
    $condorBinValue = Join-Path $InstallDir "bin"
    $condorBinLiteral = "'" + ($condorBinValue -replace "'", "''") + "'"

    $scriptText = $scriptTemplate.Replace("__SECRET_PATH__", $secretPathLiteral)
    $scriptText = $scriptText.Replace("__RESULT_PATH__", $resultPathLiteral)
    $scriptText = $scriptText.Replace("__CONDOR_BIN__", $condorBinLiteral)

    Set-Content `
        -LiteralPath $scriptPath `
        -Value $scriptText `
        -Encoding UTF8

    $helperTokens = $null
    $helperErrors = $null

    [System.Management.Automation.Language.Parser]::ParseFile(
        $scriptPath,
        [ref]$helperTokens,
        [ref]$helperErrors
    ) | Out-Null

    if ($helperErrors.Count -gt 0) {
        $messages = @(
            $helperErrors |
                ForEach-Object {
                    "line $($_.Extent.StartLineNumber): $($_.Message)"
                }
        ) -join "; "

        throw "提交凭据辅助脚本语法错误：$messages"
    }

    $exitCode = Invoke-AsSubmitAccount `
        -ScriptPath $scriptPath `
        -Password $Password `
        -TimeoutSeconds 180

    if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) {
        throw "提交凭据初始化没有生成结果文件。"
    }

    $result = Get-Content -LiteralPath $resultPath -Raw |
        ConvertFrom-Json

    if ($exitCode -ne 0 -or -not $result.success) {
        throw "HTCondor 提交凭据保存失败：$($result.error)"
    }

    Add-CompletedStep "credential_stored"
    Write-Ok "HTCondor 提交凭据有效。"
}

function Test-CondorSecurity {
    Write-Step "检查 NTSSPI 安全连接"
    Save-State `
        -Stage "validating_security" `
        -Message "正在检查 HTCondor WRITE 安全连接。"

    $pingExe = Join-Path $InstallDir "bin\condor_ping.exe"

    # 不通过 -name 查询 Collector。单机安装时直接连接本机 Schedd 更可靠。
    # 使用 ProcessStartInfo 分别接收 stdout/stderr，避免普通 WARNING 被
    # PowerShell 的 ErrorActionPreference=Stop 误判为致命错误。
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $pingExe
    $startInfo.Arguments = "-table WRITE"
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo

    try {
        [void]$process.Start()
        $standardOutput = $process.StandardOutput.ReadToEnd()
        $standardError = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        $pingExitCode = $process.ExitCode
    }
    finally {
        $process.Dispose()
    }

    $output = (
        ($standardOutput + [Environment]::NewLine + $standardError)
    ).Trim()

    if (
        $pingExitCode -ne 0 -or
        $output -notmatch "NTSSPI" -or
        $output -notmatch "ALLOW"
    ) {
        throw "HTCondor NTSSPI WRITE 检查失败：$output"
    }

    Add-CompletedStep "ntsspi_write_allowed"
    Write-Ok "NTSSPI WRITE 认证检查通过。"
}

function Invoke-SmokeTest {
    param([string]$Password)

    Write-Step "运行 HTCondor 端到端自检任务"
    Save-State `
        -Stage "running_smoke_test" `
        -Message "正在提交并执行 HTCondor 自检任务。"

    $testId = [Guid]::NewGuid().ToString("N").Substring(0, 10)
    $smokeDir = Join-Path $RuntimeDir "install_test_$testId"
    $scriptPath = Join-Path $RuntimeDir "run_smoke_test.ps1"

    New-Item -ItemType Directory -Force -Path $smokeDir | Out-Null

    & icacls.exe $smokeDir `
        /grant "${WindowsSubmitAccount}:(OI)(CI)M" `
        /T `
        /C |
        Out-Null

    $scriptTemplate = @'
$ErrorActionPreference = "Stop"

$condorBin = __CONDOR_BIN__
$testDir = __TEST_DIR__
$resultPath = __RESULT_PATH__
$submitExe = Join-Path $condorBin "condor_submit.exe"
$waitExe = Join-Path $condorBin "condor_wait.exe"
$historyExe = Join-Path $condorBin "condor_history.exe"

function Save-Result {
    param($Data)

    $Data |
        ConvertTo-Json -Depth 12 |
        Set-Content -LiteralPath $resultPath -Encoding UTF8
}

try {
    New-Item -ItemType Directory -Force -Path $testDir | Out-Null
    Set-Location $testDir

    $cmdLines = @(
        "@echo off",
        "(",
        "    echo success=true",
        "    echo computer=%COMPUTERNAME%",
        "    echo time=%DATE% %TIME%",
        "    echo working_directory=%CD%",
        ") > result.txt",
        "exit /b 0"
    )

    $cmdLines |
        Set-Content -LiteralPath (Join-Path $testDir "self_test.cmd") -Encoding ASCII

    $initialDir = $testDir.Replace("\", "/")

    $submitLines = @(
        "universe = vanilla",
        "executable = C:/Windows/System32/cmd.exe",
        "arguments = /D /C self_test.cmd",
        "",
        "initialdir = $initialDir",
        "",
        "should_transfer_files = YES",
        "when_to_transfer_output = ON_EXIT",
        "preserve_relative_paths = True",
        "",
        "transfer_input_files = self_test.cmd",
        "transfer_output_files = result.txt",
        "",
        "output = stdout.txt",
        "error = stderr.txt",
        "log = event.log",
        "",
        "request_cpus = 1",
        "request_memory = 128MB",
        "request_disk = 100MB",
        "",
        "run_as_owner = false",
        "",
        "queue 1"
    )

    $submitFile = Join-Path $testDir "self_test.sub"
    $eventLog = Join-Path $testDir "event.log"

    $submitLines |
        Set-Content -LiteralPath $submitFile -Encoding ASCII

    $submitOutput = (& $submitExe $submitFile 2>&1 | Out-String).Trim()

    if ($LASTEXITCODE -ne 0) {
        throw "condor_submit failed: $submitOutput"
    }

    $clusterMatch = [regex]::Match(
        $submitOutput,
        'cluster\s+(\d+)',
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )

    if (-not $clusterMatch.Success) {
        throw "Unable to parse ClusterId: $submitOutput"
    }

    $clusterId = [int]$clusterMatch.Groups[1].Value
    $waitOutput = (& $waitExe -wait 90 $eventLog 2>&1 | Out-String).Trim()

    if (
        $LASTEXITCODE -ne 0 -or
        $waitOutput -notmatch "All jobs done"
    ) {
        throw "condor_wait failed: $waitOutput"
    }

    $taskResultPath = Join-Path $testDir "result.txt"
    $stderrPath = Join-Path $testDir "stderr.txt"

    if (-not (Test-Path -LiteralPath $taskResultPath -PathType Leaf)) {
        throw "Self-test result.txt was not returned."
    }

    $taskResult = Get-Content -LiteralPath $taskResultPath -Raw
    $stderr = if (Test-Path -LiteralPath $stderrPath) {
        Get-Content -LiteralPath $stderrPath -Raw
    }
    else {
        ""
    }

    if ($taskResult -notmatch "success=true") {
        throw "Self-test result does not contain success=true."
    }

    if (-not [string]::IsNullOrWhiteSpace($stderr)) {
        throw "Self-test stderr is not empty: $stderr"
    }

    $computerMatch = [regex]::Match($taskResult, '(?m)^computer=(.+)$')
    $executeComputer = if ($computerMatch.Success) {
        $computerMatch.Groups[1].Value.Trim()
    }
    else {
        ""
    }

    $jobId = "$clusterId.0"
    $historyOutput = (& $historyExe $jobId -af ClusterId ProcId Owner JobStatus ExitCode 2>&1 | Out-String).Trim()

    Save-Result -Data @{
        success = $true
        cluster_id = $clusterId
        proc_id = 0
        execute_computer = $executeComputer
        submit_output = $submitOutput
        wait_output = $waitOutput
        history = $historyOutput
        test_dir = $testDir
        completed_at = [DateTime]::UtcNow.ToString("o")
    }

    exit 0
}
catch {
    Save-Result -Data @{
        success = $false
        error = $_.Exception.Message
        test_dir = $testDir
        completed_at = [DateTime]::UtcNow.ToString("o")
    }

    exit 1
}
'@

    $condorBinValue = Join-Path $InstallDir "bin"
    $condorBinLiteral = "'" + ($condorBinValue -replace "'", "''") + "'"
    $testDirLiteral = "'" + ($smokeDir -replace "'", "''") + "'"
    $resultPathLiteral = "'" + ($SmokeResultPath -replace "'", "''") + "'"

    $scriptText = $scriptTemplate.Replace("__CONDOR_BIN__", $condorBinLiteral)
    $scriptText = $scriptText.Replace("__TEST_DIR__", $testDirLiteral)
    $scriptText = $scriptText.Replace("__RESULT_PATH__", $resultPathLiteral)

    Set-Content `
        -LiteralPath $scriptPath `
        -Value $scriptText `
        -Encoding UTF8

    $helperTokens = $null
    $helperErrors = $null

    [System.Management.Automation.Language.Parser]::ParseFile(
        $scriptPath,
        [ref]$helperTokens,
        [ref]$helperErrors
    ) | Out-Null

    if ($helperErrors.Count -gt 0) {
        $messages = @(
            $helperErrors |
                ForEach-Object {
                    "line $($_.Extent.StartLineNumber): $($_.Message)"
                }
        ) -join "; "

        throw "HTCondor 自检辅助脚本语法错误：$messages"
    }

    Remove-Item `
        -LiteralPath $SmokeResultPath `
        -Force `
        -ErrorAction SilentlyContinue

    $exitCode = Invoke-AsSubmitAccount `
        -ScriptPath $scriptPath `
        -Password $Password `
        -TimeoutSeconds 180

    if (-not (Test-Path -LiteralPath $SmokeResultPath -PathType Leaf)) {
        throw "HTCondor 自检任务没有生成结果文件。"
    }

    $result = Get-Content -LiteralPath $SmokeResultPath -Raw |
        ConvertFrom-Json

    if ($exitCode -ne 0 -or -not $result.success) {
        throw "HTCondor 自检任务失败：$($result.error)"
    }

    Add-CompletedStep "smoke_test_passed"
    Write-Ok (
        "HTCondor 自检通过，ClusterId=" +
        $result.cluster_id +
        "，执行节点=" +
        $result.execute_computer
    )

    return $result
}

function Remove-TemporarySetupFiles {
    foreach ($path in @(
        (Join-Path $RuntimeDir "setup_user_record_as_system.ps1"),
        (Join-Path $RuntimeDir "store_submit_credential.ps1"),
        (Join-Path $RuntimeDir "run_smoke_test.ps1")
    )) {
        Remove-Item `
            -LiteralPath $path `
            -Force `
            -ErrorAction SilentlyContinue
    }
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-IsAdministrator)) {
    if ($NoAutoElevate) {
        Write-JsonFile -Path $ResultPath -Value @{
            success = $false
            status = "admin_required"
            message = "HTCondor 一键安装需要 Windows 管理员权限。"
            completed_at = [DateTime]::UtcNow.ToString("o")
        }

        Write-Error "HTCondor 一键安装需要 Windows 管理员权限。"
        exit 740
    }

    Write-Host "正在请求一次 Windows 管理员权限..." -ForegroundColor Yellow

    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Quote-ProcessArgument $PSCommandPath),
        "-ProjectRoot", (Quote-ProcessArgument $ProjectRoot),
        "-InstallDir", (Quote-ProcessArgument $InstallDir),
        "-SubmitAccount", (Quote-ProcessArgument $SubmitAccount),
        "-BackendUserName", (Quote-ProcessArgument $BackendUserName),
        "-BackendUserSid", (Quote-ProcessArgument $BackendUserSid),
        "-NoAutoElevate"
    )

    if ($ForceReinstall) {
        $arguments += "-ForceReinstall"
    }

    try {
        $process = Start-Process `
            -FilePath "powershell.exe" `
            -Verb RunAs `
            -ArgumentList $arguments `
            -Wait `
            -PassThru

        exit $process.ExitCode
    }
    catch {
        Write-JsonFile -Path $ResultPath -Value @{
            success = $false
            status = "elevation_cancelled"
            message = "用户取消了管理员授权，HTCondor 未完成安装。"
            error = $_.Exception.Message
            completed_at = [DateTime]::UtcNow.ToString("o")
        }

        exit 740
    }
}

try {
    Write-Host ""
    Write-Host "HTCondor one-click setup"
    Write-Info "Project root: $ProjectRoot"
    Write-Info "Machine: $ComputerName"
    Write-Info "Submit identity: $SubmitIdentity"

    Save-State `
        -Stage "preflight" `
        -Message "正在检查安装环境。"

    if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
        throw "项目根目录不存在：$ProjectRoot"
    }

    $bundle = Assert-Bundle
    Add-CompletedStep "package_verified"
    Save-State `
        -Stage "package_verified" `
        -Message "HTCondor 安装包完整性校验通过。"

    Write-Ok "HTCondor MSI SHA-256 校验通过。"

    $runtime = Get-CondorRuntime

    if (
        $runtime.installed -and
        -not $ForceReinstall
    ) {
        if (
            $runtime.version_output -notmatch
            [regex]::Escape([string]$bundle.product_version)
        ) {
            throw (
                "已安装的 HTCondor 版本与内置版本不一致。" +
                " bundled=$($bundle.product_version) " +
                "installed=$($runtime.version_output)"
            )
        }

        Add-CompletedStep "files_installed"
        Add-CompletedStep "service_created"

        Write-Ok "检测到正确版本的 HTCondor，跳过文件安装。"
    }
    else {
        Install-CondorFromAdministrativeImage `
            -ExpectedVersion ([string]$bundle.product_version)
    }

    Write-Step "写入动态本机配置"
    Write-CondorBaseConfig -KnownUsersOnly $false
    Restart-CondorService
    Wait-ForCondorReady
    Add-CompletedStep "dynamic_config_written"
    Add-CompletedStep "service_running"

    $password = Ensure-SubmitAccount

    Ensure-CondorUserRecord
    Ensure-SubmitCredential -Password $password

    Write-Step "应用最终安全配置"
    Write-CondorBaseConfig -KnownUsersOnly $true
    Restart-CondorService
    Wait-ForCondorReady
    Add-CompletedStep "final_security_config_applied"

    Test-CondorSecurity
    $smokeResult = Invoke-SmokeTest -Password $password

    $runtime = Get-CondorRuntime

    $finalResult = [ordered]@{
        success = $true
        status = "fully_validated"
        message = "HTCondor 已完成一键安装和端到端自检。"
        machine = $ComputerName
        uid_domain = $UidDomain
        htcondor_version = [string]$bundle.product_version
        installed_version_output = [string]$runtime.version_output
        install_dir = $InstallDir
        service_name = $ServiceName
        service_status = [string]$runtime.service_status
        submit_account = $SubmitAccount
        submit_identity = $SubmitIdentity
        backend_user = $BackendUserName
        backend_user_sid = $BackendUserSid
        credential_valid = $true
        user_record_valid = $true
        ntsspi_write_allowed = $true
        known_users_only = $true
        secret_file = $SecretPath
        smoke_test = $smokeResult
        completed_steps = @($CompletedSteps)
        extraction_log = $ExtractLog
        completed_at = [DateTime]::UtcNow.ToString("o")
    }

    Add-CompletedStep "installation_complete"
    $finalResult.completed_steps = @($CompletedSteps)

    Write-JsonFile -Path $ResultPath -Value $finalResult
    Save-State `
        -Stage "installation_complete" `
        -Message "HTCondor 一键安装和端到端自检成功。" `
        -Success $true

    Remove-TemporarySetupFiles

    Write-Host ""
    Write-Ok "HTCondor 一键安装和自检成功。"
    Write-Info "Result: $ResultPath"
    Write-Info "ClusterId: $($smokeResult.cluster_id)"
    Write-Info "Execute computer: $($smokeResult.execute_computer)"

    exit 0
}
catch {
    $errorMessage = $_.Exception.Message

    $failureResult = [ordered]@{
        success = $false
        status = "failed"
        stage = $CurrentStage
        message = $errorMessage
        error = "$($_.Exception.GetType().Name): $errorMessage"
        machine = $ComputerName
        uid_domain = $UidDomain
        install_dir = $InstallDir
        submit_account = $SubmitAccount
        submit_identity = $SubmitIdentity
        completed_steps = @($CompletedSteps)
        extraction_log = $ExtractLog
        master_log = Join-Path $InstallDir "log\MasterLog"
        schedd_log = Join-Path $InstallDir "log\SchedLog"
        completed_at = [DateTime]::UtcNow.ToString("o")
    }

    Write-JsonFile -Path $ResultPath -Value $failureResult
    Save-State `
        -Stage $CurrentStage `
        -Message $errorMessage `
        -Success $false

    Write-Host ""
    Write-Host "[ERROR] $errorMessage" -ForegroundColor Red
    Write-Info "Result: $ResultPath"

    exit 1
}
