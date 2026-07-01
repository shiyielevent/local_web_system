param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [string]$InstallDir = "C:\Condor",

    [string]$BootstrapPoolName = "RemoteSensingBootstrap",

    [switch]$NoAutoElevate
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [hashtable]$Value
    )

    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }

    $Value | ConvertTo-Json -Depth 10 |
        Set-Content -LiteralPath $Path -Encoding UTF8
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

function Find-CondorVersionExe {
    param([string]$PreferredInstallDir)

    $candidates = @(
        (Join-Path $PreferredInstallDir "bin\condor_version.exe"),
        "C:\Condor\bin\condor_version.exe",
        "C:\condor\bin\condor_version.exe",
        (Join-Path $env:ProgramFiles "HTCondor\bin\condor_version.exe"),
        (Join-Path $env:ProgramFiles "Condor\bin\condor_version.exe")
    )

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }

    return ""
}

$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)

$runtimeDir = Join-Path $ProjectRoot "backend\runtime\htcondor"
$logsDir = Join-Path $ProjectRoot "backend\logs\htcondor"
$resultPath = Join-Path $runtimeDir "install_result.json"

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

if (-not (Test-IsAdministrator)) {
    if ($NoAutoElevate) {
        $result = @{
            success = $false
            status = "admin_required"
            message = "安装 HTCondor 需要 Windows 管理员权限。"
            exit_code = 740
            project_root = $ProjectRoot
            install_dir = $InstallDir
            completed_at = [DateTime]::UtcNow.ToString("o")
        }
        Write-JsonFile -Path $resultPath -Value $result
        Write-Error $result.message
        exit 740
    }

    Write-Host "正在请求 Windows 管理员权限..." -ForegroundColor Yellow

    $elevatedArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Quote-ProcessArgument $PSCommandPath),
        "-ProjectRoot", (Quote-ProcessArgument $ProjectRoot),
        "-InstallDir", (Quote-ProcessArgument $InstallDir),
        "-BootstrapPoolName", (Quote-ProcessArgument $BootstrapPoolName),
        "-NoAutoElevate"
    )

    try {
        $process = Start-Process `
            -FilePath "powershell.exe" `
            -Verb RunAs `
            -ArgumentList $elevatedArgs `
            -Wait `
            -PassThru

        exit $process.ExitCode
    }
    catch {
        $result = @{
            success = $false
            status = "elevation_cancelled"
            message = "未获得管理员权限，HTCondor 未安装。"
            error = "$($_.Exception.GetType().Name): $($_.Exception.Message)"
            exit_code = 740
            project_root = $ProjectRoot
            install_dir = $InstallDir
            completed_at = [DateTime]::UtcNow.ToString("o")
        }
        Write-JsonFile -Path $resultPath -Value $result
        Write-Error $result.message
        exit 740
    }
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logsDir "htcondor_install_$timestamp.log"

$msiPath = Join-Path $ProjectRoot "third_party\htcondor\condor-Windows-x64.msi"
$manifestPath = Join-Path $ProjectRoot "third_party\htcondor\manifest.json"

try {
    if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
        throw "项目根目录不存在：$ProjectRoot"
    }

    if (-not (Test-Path -LiteralPath $msiPath -PathType Leaf)) {
        throw "找不到内置 HTCondor MSI：$msiPath"
    }

    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "找不到 HTCondor 清单：$manifestPath"
    }

    $manifest = Get-Content -LiteralPath $manifestPath -Raw |
        ConvertFrom-Json

    $expectedVersion = [string]$manifest.product_version
    $expectedHash = ([string]$manifest.sha256).Trim().ToLowerInvariant()

    if (-not $expectedVersion) {
        throw "manifest.json 中没有 product_version"
    }

    if (-not $expectedHash) {
        throw "manifest.json 中没有 sha256"
    }

    $actualHash = (
        Get-FileHash -LiteralPath $msiPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()

    if ($actualHash -ne $expectedHash) {
        throw (
            "HTCondor MSI 完整性校验失败。" +
            " expected=$expectedHash actual=$actualHash"
        )
    }

    $existingVersionExe = Find-CondorVersionExe -PreferredInstallDir $InstallDir
    $existingService = Get-Service -Name "Condor" -ErrorAction SilentlyContinue

    if ($existingVersionExe -or $existingService) {
        $versionOutput = ""
        if ($existingVersionExe) {
            $versionOutput = (
                & $existingVersionExe 2>&1 | Out-String
            ).Trim()
        }

        $result = @{
            success = $true
            status = "already_installed"
            message = "检测到本机已经安装 HTCondor，本次未重复安装。"
            expected_version = $expectedVersion
            installed_version_output = $versionOutput
            version_exe = $existingVersionExe
            service_exists = [bool]$existingService
            service_status = if ($existingService) {
                [string]$existingService.Status
            } else {
                "NotFound"
            }
            install_dir = $InstallDir
            log_path = ""
            completed_at = [DateTime]::UtcNow.ToString("o")
        }
        Write-JsonFile -Path $resultPath -Value $result
        Write-Host $result.message -ForegroundColor Cyan
        exit 0
    }

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

    # 首次只安装为隔离的“引导池”：
    # - NEWPOOL=Y：保证所有电脑使用同一个安装包都能独立完成安装；
    # - RUNJOBS=N / SUBMITJOBS=N：安装阶段不执行、不提交任务；
    # - 后续由管理员页面的“创建集群/加入集群”重新写入正式角色配置。
    $msiArguments = @(
        "/i", ('"{0}"' -f $msiPath),
        "/qn",
        "/norestart",
        "/L*v", ('"{0}"' -f $logPath),
        "NEWPOOL=Y",
        ('POOLNAME="{0}"' -f $BootstrapPoolName),
        "RUNJOBS=N",
        "VACATEJOBS=Y",
        "SUBMITJOBS=N",
        'ALLOWREAD=$(IP_ADDRESS)',
        'ALLOWWRITE=$(IP_ADDRESS)',
        'ALLOWADMINISTRATOR=$(IP_ADDRESS)',
        ('INSTALLDIR="{0}"' -f $InstallDir),
        'POOLHOSTNAME=$(IP_ADDRESS)',
        "ACCOUNTINGDOMAIN=none",
        "USEVMUNIVERSE=N",
        "VMMEMORY=128",
        "VMMAXNUMBER=1",
        "VMNETWORKING=N"
    )

    Write-Host "正在静默安装 HTCondor $expectedVersion ..." -ForegroundColor Cyan
    Write-Host "安装日志：$logPath"

    $msiProcess = Start-Process `
        -FilePath "msiexec.exe" `
        -ArgumentList $msiArguments `
        -Wait `
        -PassThru

    $msiExitCode = [int]$msiProcess.ExitCode
    $acceptedExitCodes = @(0, 1641, 3010)

    if ($acceptedExitCodes -notcontains $msiExitCode) {
        throw "HTCondor MSI 安装失败，msiexec 退出码：$msiExitCode。请查看日志：$logPath"
    }

    $service = Get-Service -Name "Condor" -ErrorAction SilentlyContinue
    if (-not $service) {
        throw "MSI 返回成功，但没有找到 Condor Windows 服务。"
    }

    if ($service.Status -ne "Running") {
        Start-Service -Name "Condor"
        $service.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Running,
            [TimeSpan]::FromSeconds(30)
        )
        $service.Refresh()
    }

    $versionExe = Find-CondorVersionExe -PreferredInstallDir $InstallDir
    if (-not $versionExe) {
        throw "安装完成，但找不到 condor_version.exe。"
    }

    $versionOutput = (
        & $versionExe 2>&1 | Out-String
    ).Trim()

    $statusExe = Join-Path (Split-Path -Parent $versionExe) "condor_status.exe"
    $statusOutput = ""
    $statusExitCode = $null

    if (Test-Path -LiteralPath $statusExe -PathType Leaf) {
        $statusProcess = Start-Process `
            -FilePath $statusExe `
            -ArgumentList @("-master") `
            -Wait `
            -PassThru `
            -NoNewWindow `
            -RedirectStandardOutput (Join-Path $runtimeDir "condor_status_stdout.txt") `
            -RedirectStandardError (Join-Path $runtimeDir "condor_status_stderr.txt")

        $statusExitCode = [int]$statusProcess.ExitCode

        $stdoutPath = Join-Path $runtimeDir "condor_status_stdout.txt"
        $stderrPath = Join-Path $runtimeDir "condor_status_stderr.txt"
        $statusOutput = (
            @(
                if (Test-Path $stdoutPath) { Get-Content $stdoutPath -Raw }
                if (Test-Path $stderrPath) { Get-Content $stderrPath -Raw }
            ) -join "`n"
        ).Trim()
    }

    $result = @{
        success = $true
        status = "installed"
        message = "HTCondor 已完成静默安装。当前仅为本机引导配置，尚未创建或加入正式集群。"
        expected_version = $expectedVersion
        installed_version_output = $versionOutput
        msi_exit_code = $msiExitCode
        reboot_required = ($msiExitCode -in @(1641, 3010))
        install_dir = $InstallDir
        version_exe = $versionExe
        service_name = "Condor"
        service_status = [string]$service.Status
        condor_status_exit_code = $statusExitCode
        condor_status_output = $statusOutput
        log_path = $logPath
        completed_at = [DateTime]::UtcNow.ToString("o")
    }

    Write-JsonFile -Path $resultPath -Value $result

    Write-Host ""
    Write-Host "HTCondor 静默安装成功。" -ForegroundColor Green
    Write-Host "版本检测：$versionOutput"
    Write-Host "Condor 服务：$($service.Status)"
    Write-Host "安装日志：$logPath"
    Write-Host "结果文件：$resultPath"
    Write-Host ""
    Write-Host "当前尚未创建或加入正式集群。" -ForegroundColor Yellow

    exit 0
}
catch {
    $message = $_.Exception.Message

    $result = @{
        success = $false
        status = "failed"
        message = $message
        error = "$($_.Exception.GetType().Name): $message"
        project_root = $ProjectRoot
        install_dir = $InstallDir
        log_path = $logPath
        completed_at = [DateTime]::UtcNow.ToString("o")
    }

    Write-JsonFile -Path $resultPath -Value $result
    Write-Error $message
    exit 1
}
