param(
    [ValidateSet("System", "Backend", "Frontend")]
    [string]$Mode = "System",

    [switch]$RebuildEnvironment,
    [switch]$SkipFrontendBuild,
    [switch]$SkipHTCondorInstall,
    [switch]$NoBrowser,
    [switch]$ForceFrontendInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

$BackendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $BackendDir
$FrontendDir = Join-Path $ProjectRoot "frontend"

$VenvDir = Join-Path $BackendDir ".venv"
$LockFile = Join-Path $BackendDir "requirements.lock.txt"
$VerifyScript = Join-Path $BackendDir "verify_env.py"
$DetectScript = Join-Path $BackendDir "detect_resources.py"
$FingerprintFile = Join-Path $VenvDir ".local_web_env.json"

$TargetPythonVersion = "3.12.4"
$SystemUrl = "http://127.0.0.1:8000"

# HTCondor is bundled with the project and installed once on first system start.
$HTCondorBundleDir = Join-Path $ProjectRoot "third_party\htcondor"
$HTCondorMsi = Join-Path $HTCondorBundleDir "condor-Windows-x64.msi"
$HTCondorManifest = Join-Path $HTCondorBundleDir "manifest.json"
$HTCondorInstallDir = "C:\Condor"
$HTCondorServiceName = "Condor"
$HTCondorBootstrapPoolName = "RemoteSensingBootstrap"

$HTCondorRuntimeDir = Join-Path $BackendDir "runtime\htcondor"
$HTCondorLogDir = Join-Path $BackendDir "logs\htcondor"
$HTCondorInstallResult = Join-Path $HTCondorRuntimeDir "install_result.json"
$HTCondorOneClickSetup = Join-Path $BackendDir "htcondor_one_click_setup.ps1"

# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "[STEP] $Text"
    Write-Host "============================================================"
}

function Write-Info([string]$Text) {
    Write-Host "[INFO] $Text"
}

function Write-Ok([string]$Text) {
    Write-Host "[OK] $Text" -ForegroundColor Green
}

function Write-Warn([string]$Text) {
    Write-Host "[WARN] $Text" -ForegroundColor Yellow
}

function Test-SystemServerReady {
    try {
        $response = Invoke-WebRequest `
            -Uri $SystemUrl `
            -UseBasicParsing `
            -TimeoutSec 3

        return (
            $response.StatusCode -eq 200 -and
            [string]$response.Content -match "本地模块 Web 系统|<div id=`"root`""
        )
    }
    catch {
        return $false
    }
}

function Set-DefaultProcessEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $current = [Environment]::GetEnvironmentVariable(
        $Name,
        [EnvironmentVariableTarget]::Process
    )

    if ([string]::IsNullOrWhiteSpace($current)) {
        [Environment]::SetEnvironmentVariable(
            $Name,
            $Value,
            [EnvironmentVariableTarget]::Process
        )
    }
}

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

    $Value |
        ConvertTo-Json -Depth 12 |
        Set-Content -LiteralPath $Path -Encoding UTF8
}

# ---------------------------------------------------------------------------
# HTCondor installation and runtime detection
# ---------------------------------------------------------------------------

function Find-CondorVersionExe {
    $candidates = New-Object System.Collections.Generic.List[string]

    try {
        $pathCommand = Get-Command "condor_version.exe" -ErrorAction Stop
        if ($pathCommand.Source) {
            [void]$candidates.Add($pathCommand.Source)
        }
    }
    catch {}

    foreach ($candidate in @(
        (Join-Path $HTCondorInstallDir "bin\condor_version.exe"),
        "C:\Condor\bin\condor_version.exe",
        "C:\condor\bin\condor_version.exe",
        (Join-Path $env:ProgramFiles "HTCondor\bin\condor_version.exe"),
        (Join-Path $env:ProgramFiles "Condor\bin\condor_version.exe")
    )) {
        if ($candidate) {
            [void]$candidates.Add($candidate)
        }
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }

    return ""
}

function Get-HTCondorRuntimeStatus {
    $versionExe = Find-CondorVersionExe
    $service = Get-Service `
        -Name $HTCondorServiceName `
        -ErrorAction SilentlyContinue

    $versionOutput = ""
    $versionCommandOk = $false

    if ($versionExe) {
        try {
            $versionOutput = (
                & $versionExe 2>&1 | Out-String
            ).Trim()
            $versionCommandOk = ($LASTEXITCODE -eq 0)
        }
        catch {
            $versionOutput = $_.Exception.Message
            $versionCommandOk = $false
        }
    }

    return [ordered]@{
        installed = [bool](
            $versionExe -and
            $versionCommandOk -and
            $service
        )
        version_exe = $versionExe
        version_output = $versionOutput
        version_command_ok = $versionCommandOk
        service_exists = [bool]$service
        service_status = if ($service) {
            [string]$service.Status
        }
        else {
            "NotInstalled"
        }
    }
}


function Get-CurrentUserForHTCondorAcl {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        return [ordered]@{
            name = [string]$identity.Name
            sid = [string]$identity.User.Value
        }
    }
    catch {
        return [ordered]@{
            name = [string]$env:USERNAME
            sid = ""
        }
    }
}

function Test-HTCondorSecretReadable {
    $secretPath = Join-Path $HTCondorRuntimeDir "submit_account_secret.bin"

    if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
        return $false
    }

    try {
        [void][System.IO.File]::ReadAllBytes($secretPath)
        return $true
    }
    catch {
        return $false
    }
}

function Assert-HTCondorBundle {
    if (-not (Test-Path -LiteralPath $HTCondorMsi -PathType Leaf)) {
        throw "Missing bundled HTCondor MSI: $HTCondorMsi"
    }

    if (-not (Test-Path -LiteralPath $HTCondorManifest -PathType Leaf)) {
        throw "Missing HTCondor manifest: $HTCondorManifest"
    }

    try {
        $manifest = Get-Content `
            -LiteralPath $HTCondorManifest `
            -Raw `
            -Encoding UTF8 |
            ConvertFrom-Json
    }
    catch {
        throw "Unable to read HTCondor manifest: $($_.Exception.Message)"
    }

    $expectedVersion = [string]$manifest.product_version
    $expectedHash = ([string]$manifest.sha256).Trim().ToLowerInvariant()

    if ([string]::IsNullOrWhiteSpace($expectedVersion)) {
        throw "manifest.json does not contain product_version."
    }

    if ([string]::IsNullOrWhiteSpace($expectedHash)) {
        throw "manifest.json does not contain sha256."
    }

    $actualHash = (
        Get-FileHash `
            -LiteralPath $HTCondorMsi `
            -Algorithm SHA256
    ).Hash.ToLowerInvariant()

    if ($actualHash -ne $expectedHash) {
        throw (
            "HTCondor MSI SHA-256 verification failed. " +
            "Expected=$expectedHash Actual=$actualHash"
        )
    }

    return [ordered]@{
        product_name = [string]$manifest.product_name
        product_version = $expectedVersion
        expected_sha256 = $expectedHash
        actual_sha256 = $actualHash
        signature_status = [string]$manifest.signature_status
    }
}

function Add-HTCondorBinToProcessPath {
    $versionExe = Find-CondorVersionExe
    if (-not $versionExe) {
        return
    }

    $binDir = Split-Path -Parent $versionExe
    $pathEntries = @($env:PATH -split ";")

    if ($pathEntries -notcontains $binDir) {
        $env:PATH = "$binDir;$env:PATH"
    }

    $pythonBindingDir = Join-Path (
        Split-Path -Parent $binDir
    ) "lib\python"

    if (Test-Path -LiteralPath $pythonBindingDir -PathType Container) {
        $existingPythonPath = [string]$env:PYTHONPATH
        $pythonPathEntries = if ($existingPythonPath) {
            @($existingPythonPath -split ";")
        }
        else {
            @()
        }

        if ($pythonPathEntries -notcontains $pythonBindingDir) {
            $env:PYTHONPATH = if ($existingPythonPath) {
                "$pythonBindingDir;$existingPythonPath"
            }
            else {
                $pythonBindingDir
            }
        }
    }
}


function Install-HTCondorManualFallback {
    param(
        [Parameter(Mandatory = $true)]
        $BundleInfo,

        [Parameter(Mandatory = $true)]
        [string]$FailedMsiLog
    )

    Write-Warn "The normal MSI path failed in MakeAdminToken."
    Write-Info "Starting the official manual Windows installation fallback."

    New-Item -ItemType Directory -Force -Path $HTCondorRuntimeDir | Out-Null
    New-Item -ItemType Directory -Force -Path $HTCondorLogDir | Out-Null

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $helperPath = Join-Path `
        $HTCondorRuntimeDir `
        "htcondor_manual_install_$timestamp.ps1"
    $manualResultPath = Join-Path `
        $HTCondorRuntimeDir `
        "manual_install_result_$timestamp.json"
    $stagingDir = Join-Path `
        $HTCondorRuntimeDir `
        "admin_image_$timestamp"
    $extractLog = Join-Path `
        $HTCondorLogDir `
        "htcondor_admin_extract_$timestamp.log"

    # This helper is generated temporarily because file-system, registry,
    # service and firewall operations require administrator permission.
    # Store the elevated helper as Base64 so its own here-strings do not
    # interfere with the parser of this bootstrap script.
    $helperBase64 = "77u/cGFyYW0oDQogICAgW1BhcmFtZXRlcihNYW5kYXRvcnkgPSAkdHJ1ZSldDQogICAgW3N0cmluZ10kTXNpUGF0aCwNCg0KICAgIFtQYXJhbWV0ZXIoTWFuZGF0b3J5ID0gJHRydWUpXQ0KICAgIFtzdHJpbmddJEluc3RhbGxEaXIsDQoNCiAgICBbUGFyYW1ldGVyKE1hbmRhdG9yeSA9ICR0cnVlKV0NCiAgICBbc3RyaW5nXSRTZXJ2aWNlTmFtZSwNCg0KICAgIFtQYXJhbWV0ZXIoTWFuZGF0b3J5ID0gJHRydWUpXQ0KICAgIFtzdHJpbmddJFN0YWdpbmdEaXIsDQoNCiAgICBbUGFyYW1ldGVyKE1hbmRhdG9yeSA9ICR0cnVlKV0NCiAgICBbc3RyaW5nXSRFeHRyYWN0TG9nLA0KDQogICAgW1BhcmFtZXRlcihNYW5kYXRvcnkgPSAkdHJ1ZSldDQogICAgW3N0cmluZ10kUmVzdWx0UGF0aCwNCg0KICAgIFtQYXJhbWV0ZXIoTWFuZGF0b3J5ID0gJHRydWUpXQ0KICAgIFtzdHJpbmddJEV4cGVjdGVkVmVyc2lvbiwNCg0KICAgIFtQYXJhbWV0ZXIoTWFuZGF0b3J5ID0gJHRydWUpXQ0KICAgIFtzdHJpbmddJEZhaWxlZE1zaUxvZw0KKQ0KDQpTZXQtU3RyaWN0TW9kZSAtVmVyc2lvbiBMYXRlc3QNCiRFcnJvckFjdGlvblByZWZlcmVuY2UgPSAiU3RvcCINCiRQcm9ncmVzc1ByZWZlcmVuY2UgPSAiU2lsZW50bHlDb250aW51ZSINCg0KZnVuY3Rpb24gU2F2ZS1SZXN1bHQgew0KICAgIHBhcmFtKA0KICAgICAgICBbUGFyYW1ldGVyKE1hbmRhdG9yeSA9ICR0cnVlKV0NCiAgICAgICAgW2hhc2h0YWJsZV0kRGF0YQ0KICAgICkNCg0KICAgICRwYXJlbnQgPSBTcGxpdC1QYXRoIC1QYXJlbnQgJFJlc3VsdFBhdGgNCiAgICBOZXctSXRlbSAtSXRlbVR5cGUgRGlyZWN0b3J5IC1Gb3JjZSAtUGF0aCAkcGFyZW50IHwgT3V0LU51bGwNCg0KICAgICREYXRhIHwNCiAgICAgICAgQ29udmVydFRvLUpzb24gLURlcHRoIDEyIHwNCiAgICAgICAgU2V0LUNvbnRlbnQgLUxpdGVyYWxQYXRoICRSZXN1bHRQYXRoIC1FbmNvZGluZyBVVEY4DQp9DQoNCmZ1bmN0aW9uIFJlbW92ZS1PbGRDb25kb3JTZXJ2aWNlIHsNCiAgICAkc2VydmljZSA9IEdldC1TZXJ2aWNlIC1OYW1lICRTZXJ2aWNlTmFtZSAtRXJyb3JBY3Rpb24gU2lsZW50bHlDb250aW51ZQ0KDQogICAgaWYgKC1ub3QgJHNlcnZpY2UpIHsNCiAgICAgICAgcmV0dXJuDQogICAgfQ0KDQogICAgaWYgKCRzZXJ2aWNlLlN0YXR1cyAtbmUgIlN0b3BwZWQiKSB7DQogICAgICAgIFN0b3AtU2VydmljZSAtTmFtZSAkU2VydmljZU5hbWUgLUZvcmNlIC1FcnJvckFjdGlvbiBTaWxlbnRseUNvbnRpbnVlDQogICAgICAgIFN0YXJ0LVNsZWVwIC1TZWNvbmRzIDINCiAgICB9DQoNCiAgICAmIHNjLmV4ZSBkZWxldGUgJFNlcnZpY2VOYW1lIHwgT3V0LU51bGwNCg0KICAgICRkZWFkbGluZSA9IChHZXQtRGF0ZSkuQWRkU2Vjb25kcygzMCkNCiAgICB3aGlsZSAoDQogICAgICAgIChHZXQtU2VydmljZSAtTmFtZSAkU2VydmljZU5hbWUgLUVycm9yQWN0aW9uIFNpbGVudGx5Q29udGludWUpIC1hbmQNCiAgICAgICAgKEdldC1EYXRlKSAtbHQgJGRlYWRsaW5lDQogICAgKSB7DQogICAgICAgIFN0YXJ0LVNsZWVwIC1TZWNvbmRzIDENCiAgICB9DQoNCiAgICBpZiAoR2V0LVNlcnZpY2UgLU5hbWUgJFNlcnZpY2VOYW1lIC1FcnJvckFjdGlvbiBTaWxlbnRseUNvbnRpbnVlKSB7DQogICAgICAgIHRocm93ICJUaGUgb2xkIENvbmRvciBzZXJ2aWNlIGNvdWxkIG5vdCBiZSByZW1vdmVkLiINCiAgICB9DQp9DQoNCmZ1bmN0aW9uIEFkZC1NYWNoaW5lUGF0aEVudHJ5IHsNCiAgICBwYXJhbSgNCiAgICAgICAgW1BhcmFtZXRlcihNYW5kYXRvcnkgPSAkdHJ1ZSldDQogICAgICAgIFtzdHJpbmddJEVudHJ5DQogICAgKQ0KDQogICAgJG1hY2hpbmVQYXRoID0gW0Vudmlyb25tZW50XTo6R2V0RW52aXJvbm1lbnRWYXJpYWJsZSgNCiAgICAgICAgIlBhdGgiLA0KICAgICAgICBbRW52aXJvbm1lbnRWYXJpYWJsZVRhcmdldF06Ok1hY2hpbmUNCiAgICApDQoNCiAgICAkaXRlbXMgPSBAKA0KICAgICAgICAkbWFjaGluZVBhdGggLXNwbGl0ICI7IiB8DQogICAgICAgIEZvckVhY2gtT2JqZWN0IHsgJF8uVHJpbSgpIH0gfA0KICAgICAgICBXaGVyZS1PYmplY3QgeyAkXyB9DQogICAgKQ0KDQogICAgaWYgKCRpdGVtcyAtbm90Y29udGFpbnMgJEVudHJ5KSB7DQogICAgICAgICRuZXdQYXRoID0gaWYgKFtzdHJpbmddOjpJc051bGxPcldoaXRlU3BhY2UoJG1hY2hpbmVQYXRoKSkgew0KICAgICAgICAgICAgJEVudHJ5DQogICAgICAgIH0NCiAgICAgICAgZWxzZSB7DQogICAgICAgICAgICAiJG1hY2hpbmVQYXRoOyRFbnRyeSINCiAgICAgICAgfQ0KDQogICAgICAgIFtFbnZpcm9ubWVudF06OlNldEVudmlyb25tZW50VmFyaWFibGUoDQogICAgICAgICAgICAiUGF0aCIsDQogICAgICAgICAgICAkbmV3UGF0aCwNCiAgICAgICAgICAgIFtFbnZpcm9ubWVudFZhcmlhYmxlVGFyZ2V0XTo6TWFjaGluZQ0KICAgICAgICApDQogICAgfQ0KfQ0KDQp0cnkgew0KICAgICRpZGVudGl0eSA9IFtTZWN1cml0eS5QcmluY2lwYWwuV2luZG93c0lkZW50aXR5XTo6R2V0Q3VycmVudCgpDQogICAgJHByaW5jaXBhbCA9IE5ldy1PYmplY3QgYA0KICAgICAgICBTZWN1cml0eS5QcmluY2lwYWwuV2luZG93c1ByaW5jaXBhbCgkaWRlbnRpdHkpDQoNCiAgICBpZiAoDQogICAgICAgIC1ub3QgJHByaW5jaXBhbC5Jc0luUm9sZSgNCiAgICAgICAgICAgIFtTZWN1cml0eS5QcmluY2lwYWwuV2luZG93c0J1aWx0SW5Sb2xlXTo6QWRtaW5pc3RyYXRvcg0KICAgICAgICApDQogICAgKSB7DQogICAgICAgIHRocm93ICJUaGUgbWFudWFsIEhUQ29uZG9yIGZhbGxiYWNrIGlzIG5vdCBydW5uaW5nIGFzIGFkbWluaXN0cmF0b3IuIg0KICAgIH0NCg0KICAgIFJlbW92ZS1PbGRDb25kb3JTZXJ2aWNlDQoNCiAgICBpZiAoVGVzdC1QYXRoIC1MaXRlcmFsUGF0aCAkU3RhZ2luZ0Rpcikgew0KICAgICAgICBSZW1vdmUtSXRlbSAtTGl0ZXJhbFBhdGggJFN0YWdpbmdEaXIgLVJlY3Vyc2UgLUZvcmNlDQogICAgfQ0KDQogICAgTmV3LUl0ZW0gLUl0ZW1UeXBlIERpcmVjdG9yeSAtRm9yY2UgLVBhdGggJFN0YWdpbmdEaXIgfCBPdXQtTnVsbA0KDQogICAgIyAvYSBjcmVhdGVzIGFuIGFkbWluaXN0cmF0aXZlIGltYWdlLiBJdCBleHRyYWN0cyB0aGUgcGFja2FnZSB3aXRob3V0DQogICAgIyBleGVjdXRpbmcgdGhlIGZhaWxpbmcgbm9ybWFsLWluc3RhbGwgTWFrZUFkbWluVG9rZW4gYWN0aW9uLg0KICAgICRleHRyYWN0QXJndW1lbnRzID0gQCgNCiAgICAgICAgIi9hIg0KICAgICAgICAoJyJ7MH0iJyAtZiAkTXNpUGF0aCkNCiAgICAgICAgIi9xbiINCiAgICAgICAgIi9ub3Jlc3RhcnQiDQogICAgICAgICgnVEFSR0VURElSPSJ7MH0iJyAtZiAkU3RhZ2luZ0RpcikNCiAgICAgICAgIi9MKnYiDQogICAgICAgICgnInswfSInIC1mICRFeHRyYWN0TG9nKQ0KICAgICkNCg0KICAgICRleHRyYWN0UHJvY2VzcyA9IFN0YXJ0LVByb2Nlc3MgYA0KICAgICAgICAtRmlsZVBhdGggIm1zaWV4ZWMuZXhlIiBgDQogICAgICAgIC1Bcmd1bWVudExpc3QgJGV4dHJhY3RBcmd1bWVudHMgYA0KICAgICAgICAtV2FpdCBgDQogICAgICAgIC1QYXNzVGhydQ0KDQogICAgJGV4dHJhY3RFeGl0Q29kZSA9IFtpbnRdJGV4dHJhY3RQcm9jZXNzLkV4aXRDb2RlDQoNCiAgICBpZiAoQCgwLCAxNjQxLCAzMDEwKSAtbm90Y29udGFpbnMgJGV4dHJhY3RFeGl0Q29kZSkgew0KICAgICAgICB0aHJvdyAoDQogICAgICAgICAgICAiQWRtaW5pc3RyYXRpdmUgZXh0cmFjdGlvbiBmYWlsZWQgd2l0aCBleGl0IGNvZGUgIiArDQogICAgICAgICAgICAiJGV4dHJhY3RFeGl0Q29kZS4gTG9nOiAkRXh0cmFjdExvZyINCiAgICAgICAgKQ0KICAgIH0NCg0KICAgICRtYXN0ZXJDYW5kaWRhdGVzID0gQCgNCiAgICAgICAgR2V0LUNoaWxkSXRlbSBgDQogICAgICAgICAgICAtTGl0ZXJhbFBhdGggJFN0YWdpbmdEaXIgYA0KICAgICAgICAgICAgLUZpbHRlciAiY29uZG9yX21hc3Rlci5leGUiIGANCiAgICAgICAgICAgIC1SZWN1cnNlIGANCiAgICAgICAgICAgIC1GaWxlIGANCiAgICAgICAgICAgIC1FcnJvckFjdGlvbiBTaWxlbnRseUNvbnRpbnVlIHwNCiAgICAgICAgV2hlcmUtT2JqZWN0IHsNCiAgICAgICAgICAgICRfLkRpcmVjdG9yeU5hbWUgLW1hdGNoICdbXFwvXWJpbiQnDQogICAgICAgIH0gfA0KICAgICAgICBTb3J0LU9iamVjdCB7ICRfLkZ1bGxOYW1lLkxlbmd0aCB9DQogICAgKQ0KDQogICAgaWYgKCRtYXN0ZXJDYW5kaWRhdGVzLkNvdW50IC1lcSAwKSB7DQogICAgICAgIHRocm93ICgNCiAgICAgICAgICAgICJjb25kb3JfbWFzdGVyLmV4ZSB3YXMgbm90IGZvdW5kIGluIHRoZSBhZG1pbmlzdHJhdGl2ZSBpbWFnZTogIiArDQogICAgICAgICAgICAkU3RhZ2luZ0Rpcg0KICAgICAgICApDQogICAgfQ0KDQogICAgJHNvdXJjZU1hc3RlciA9ICRtYXN0ZXJDYW5kaWRhdGVzWzBdDQogICAgJHNvdXJjZUJpbiA9IFNwbGl0LVBhdGggLVBhcmVudCAkc291cmNlTWFzdGVyLkZ1bGxOYW1lDQogICAgJHNvdXJjZVJvb3QgPSBTcGxpdC1QYXRoIC1QYXJlbnQgJHNvdXJjZUJpbg0KDQogICAgaWYgKC1ub3QgKFRlc3QtUGF0aCAtTGl0ZXJhbFBhdGggJHNvdXJjZVJvb3QgLVBhdGhUeXBlIENvbnRhaW5lcikpIHsNCiAgICAgICAgdGhyb3cgIlRoZSBleHRyYWN0ZWQgSFRDb25kb3IgcmVsZWFzZSBkaXJlY3RvcnkgY291bGQgbm90IGJlIGZvdW5kLiINCiAgICB9DQoNCiAgICBpZiAoVGVzdC1QYXRoIC1MaXRlcmFsUGF0aCAkSW5zdGFsbERpcikgew0KICAgICAgICAkYmFja3VwRGlyID0gKA0KICAgICAgICAgICAgIiR7SW5zdGFsbERpcn1fZmFpbGVkXyIgKw0KICAgICAgICAgICAgKEdldC1EYXRlIC1Gb3JtYXQgInl5eXlNTWRkX0hIbW1zcyIpDQogICAgICAgICkNCg0KICAgICAgICBNb3ZlLUl0ZW0gYA0KICAgICAgICAgICAgLUxpdGVyYWxQYXRoICRJbnN0YWxsRGlyIGANCiAgICAgICAgICAgIC1EZXN0aW5hdGlvbiAkYmFja3VwRGlyIGANCiAgICAgICAgICAgIC1Gb3JjZQ0KICAgIH0NCg0KICAgIE5ldy1JdGVtIC1JdGVtVHlwZSBEaXJlY3RvcnkgLUZvcmNlIC1QYXRoICRJbnN0YWxsRGlyIHwgT3V0LU51bGwNCg0KICAgIEdldC1DaGlsZEl0ZW0gLUxpdGVyYWxQYXRoICRzb3VyY2VSb290IC1Gb3JjZSB8DQogICAgICAgIEZvckVhY2gtT2JqZWN0IHsNCiAgICAgICAgICAgIENvcHktSXRlbSBgDQogICAgICAgICAgICAgICAgLUxpdGVyYWxQYXRoICRfLkZ1bGxOYW1lIGANCiAgICAgICAgICAgICAgICAtRGVzdGluYXRpb24gJEluc3RhbGxEaXIgYA0KICAgICAgICAgICAgICAgIC1SZWN1cnNlIGANCiAgICAgICAgICAgICAgICAtRm9yY2UNCiAgICAgICAgfQ0KDQogICAgZm9yZWFjaCAoJG5hbWUgaW4gQCgNCiAgICAgICAgImxvZyIsDQogICAgICAgICJzcG9vbCIsDQogICAgICAgICJleGVjdXRlIiwNCiAgICAgICAgImxvY2FsIiwNCiAgICAgICAgInRva2Vucy5kIiwNCiAgICAgICAgInRva2Vucy5zayINCiAgICApKSB7DQogICAgICAgIE5ldy1JdGVtIGANCiAgICAgICAgICAgIC1JdGVtVHlwZSBEaXJlY3RvcnkgYA0KICAgICAgICAgICAgLUZvcmNlIGANCiAgICAgICAgICAgIC1QYXRoIChKb2luLVBhdGggJEluc3RhbGxEaXIgJG5hbWUpIHwNCiAgICAgICAgICAgIE91dC1OdWxsDQogICAgfQ0KDQogICAgJGNvbmZpZ1BhdGggPSBKb2luLVBhdGggJEluc3RhbGxEaXIgImNvbmRvcl9jb25maWciDQogICAgJGxvY2FsQ29uZmlnUGF0aCA9IEpvaW4tUGF0aCAkSW5zdGFsbERpciAiY29uZG9yX2NvbmZpZy5sb2NhbCINCg0KICAgICRjb25maWdUZXh0ID0gQCcNCiMgR2VuZXJhdGVkIGJ5IGxvY2FsX3dlYl9tb2R1bGVfc3lzdGVtLg0KIyBUaGUgcGxhdGZvcm0gd2lsbCBsYXRlciByZXBsYWNlIHRoZSBib290c3RyYXAgcm9sZSB3aXRoIHRoZSBzZWxlY3RlZA0KIyBDZW50cmFsIE1hbmFnZXIsIFN1Ym1pdCBvciBFeGVjdXRlIHJvbGUuDQoNClJFTEVBU0VfRElSID0gX19JTlNUQUxMX0RJUl9fDQpMT0NBTF9ESVIgPSBfX0lOU1RBTExfRElSX18NCg0KQklOID0gJChSRUxFQVNFX0RJUilcYmluDQpTQklOID0gJChSRUxFQVNFX0RJUilcYmluDQpMSUIgPSAkKFJFTEVBU0VfRElSKVxsaWINCg0KTE9HID0gJChMT0NBTF9ESVIpXGxvZw0KU1BPT0wgPSAkKExPQ0FMX0RJUilcc3Bvb2wNCkVYRUNVVEUgPSAkKExPQ0FMX0RJUilcZXhlY3V0ZQ0KDQpMT0NBTF9DT05GSUdfRklMRSA9IF9fTE9DQUxfQ09ORklHX18NClJFUVVJUkVfTE9DQUxfQ09ORklHX0ZJTEUgPSBUUlVFDQonQA0KDQogICAgJGNvbmZpZ1RleHQgPSAkY29uZmlnVGV4dC5SZXBsYWNlKA0KICAgICAgICAiX19JTlNUQUxMX0RJUl9fIiwNCiAgICAgICAgJEluc3RhbGxEaXINCiAgICApDQogICAgJGNvbmZpZ1RleHQgPSAkY29uZmlnVGV4dC5SZXBsYWNlKA0KICAgICAgICAiX19MT0NBTF9DT05GSUdfXyIsDQogICAgICAgICRsb2NhbENvbmZpZ1BhdGgNCiAgICApDQoNCiAgICBTZXQtQ29udGVudCBgDQogICAgICAgIC1MaXRlcmFsUGF0aCAkY29uZmlnUGF0aCBgDQogICAgICAgIC1WYWx1ZSAkY29uZmlnVGV4dCBgDQogICAgICAgIC1FbmNvZGluZyBBU0NJSQ0KDQogICAgIyBVc2UgYSBsb29wYmFjay1vbmx5IHBlcnNvbmFsIHBvb2wgZm9yIGluc3RhbGxhdGlvbiB2YWxpZGF0aW9uLg0KICAgICMgVGhpcyBhdm9pZHMgZXhwb3NpbmcgYW4gdW5hdXRoZW50aWNhdGVkIGNvbGxlY3RvciB0byB0aGUgTEFOLg0KICAgICRsb2NhbENvbmZpZ1RleHQgPSBAJw0KdXNlIFJPTEU6IFBlcnNvbmFsDQoNCkNPTkRPUl9IT1NUID0gMTI3LjAuMC4xDQpDT0xMRUNUT1JfSE9TVCA9ICQoQ09ORE9SX0hPU1QpDQpORVRXT1JLX0lOVEVSRkFDRSA9IDEyNy4wLjAuMQ0KDQpVSURfRE9NQUlOID0gbG9jYWwNCkZJTEVTWVNURU1fRE9NQUlOID0gbG9jYWwNCg0KU0VDX0RFRkFVTFRfQVVUSEVOVElDQVRJT04gPSBPUFRJT05BTA0KU0VDX0RFRkFVTFRfQVVUSEVOVElDQVRJT05fTUVUSE9EUyA9IE5UU1NQSSwgQ0xBSU1UT0JFDQpBTExPV19SRUFEID0gKg0KQUxMT1dfV1JJVEUgPSAqDQpBTExPV19BRE1JTklTVFJBVE9SID0gKg0KDQpTVEFSVCA9IFRSVUUNClNVU1BFTkQgPSBGQUxTRQ0KUFJFRU1QVCA9IEZBTFNFDQpLSUxMID0gRkFMU0UNCidADQoNCiAgICBTZXQtQ29udGVudCBgDQogICAgICAgIC1MaXRlcmFsUGF0aCAkbG9jYWxDb25maWdQYXRoIGANCiAgICAgICAgLVZhbHVlICRsb2NhbENvbmZpZ1RleHQgYA0KICAgICAgICAtRW5jb2RpbmcgQVNDSUkNCg0KICAgICRyZWdpc3RyeVBhdGggPSAiSEtMTTpcU09GVFdBUkVcQ29uZG9yIg0KICAgIE5ldy1JdGVtIC1QYXRoICRyZWdpc3RyeVBhdGggLUZvcmNlIHwgT3V0LU51bGwNCg0KICAgIE5ldy1JdGVtUHJvcGVydHkgYA0KICAgICAgICAtUGF0aCAkcmVnaXN0cnlQYXRoIGANCiAgICAgICAgLU5hbWUgIkNPTkRPUl9DT05GSUciIGANCiAgICAgICAgLVZhbHVlICRjb25maWdQYXRoIGANCiAgICAgICAgLVByb3BlcnR5VHlwZSBTdHJpbmcgYA0KICAgICAgICAtRm9yY2UgfA0KICAgICAgICBPdXQtTnVsbA0KDQogICAgTmV3LUl0ZW1Qcm9wZXJ0eSBgDQogICAgICAgIC1QYXRoICRyZWdpc3RyeVBhdGggYA0KICAgICAgICAtTmFtZSAiUkVMRUFTRV9ESVIiIGANCiAgICAgICAgLVZhbHVlICRJbnN0YWxsRGlyIGANCiAgICAgICAgLVByb3BlcnR5VHlwZSBTdHJpbmcgYA0KICAgICAgICAtRm9yY2UgfA0KICAgICAgICBPdXQtTnVsbA0KDQogICAgJGJpbkRpciA9IEpvaW4tUGF0aCAkSW5zdGFsbERpciAiYmluIg0KICAgICRtYXN0ZXJQYXRoID0gSm9pbi1QYXRoICRiaW5EaXIgImNvbmRvcl9tYXN0ZXIuZXhlIg0KICAgICR2ZXJzaW9uUGF0aCA9IEpvaW4tUGF0aCAkYmluRGlyICJjb25kb3JfdmVyc2lvbi5leGUiDQogICAgJGNvbmZpZ1ZhbFBhdGggPSBKb2luLVBhdGggJGJpbkRpciAiY29uZG9yX2NvbmZpZ192YWwuZXhlIg0KDQogICAgZm9yZWFjaCAoJHJlcXVpcmVkRmlsZSBpbiBAKA0KICAgICAgICAkbWFzdGVyUGF0aCwNCiAgICAgICAgJHZlcnNpb25QYXRoLA0KICAgICAgICAkY29uZmlnVmFsUGF0aA0KICAgICkpIHsNCiAgICAgICAgaWYgKC1ub3QgKFRlc3QtUGF0aCAtTGl0ZXJhbFBhdGggJHJlcXVpcmVkRmlsZSAtUGF0aFR5cGUgTGVhZikpIHsNCiAgICAgICAgICAgIHRocm93ICJSZXF1aXJlZCBIVENvbmRvciBmaWxlIGlzIG1pc3Npbmc6ICRyZXF1aXJlZEZpbGUiDQogICAgICAgIH0NCiAgICB9DQoNCiAgICBBZGQtTWFjaGluZVBhdGhFbnRyeSAtRW50cnkgJGJpbkRpcg0KDQogICAgIyBIVENvbmRvcidzIG9mZmljaWFsIG1hbnVhbCBXaW5kb3dzIGluc3RhbGxhdGlvbiB1c2VzIGEgTG9jYWxTeXN0ZW0NCiAgICAjIGF1dG9tYXRpYyBzZXJ2aWNlIHdob3NlIGV4ZWN1dGFibGUgaXMgY29uZG9yX21hc3Rlci5leGUuDQogICAgTmV3LVNlcnZpY2UgYA0KICAgICAgICAtTmFtZSAkU2VydmljZU5hbWUgYA0KICAgICAgICAtQmluYXJ5UGF0aE5hbWUgKCciezB9IicgLWYgJG1hc3RlclBhdGgpIGANCiAgICAgICAgLURpc3BsYXlOYW1lICJDb25kb3IiIGANCiAgICAgICAgLURlc2NyaXB0aW9uICJIVENvbmRvciBtYXN0ZXIgc2VydmljZSIgYA0KICAgICAgICAtU3RhcnR1cFR5cGUgQXV0b21hdGljIHwNCiAgICAgICAgT3V0LU51bGwNCg0KICAgICYgc2MuZXhlIGRlc2NyaXB0aW9uIGANCiAgICAgICAgJFNlcnZpY2VOYW1lIGANCiAgICAgICAgIkhUQ29uZG9yIG1hc3RlciBzZXJ2aWNlIGZvciBsb2NhbF93ZWJfbW9kdWxlX3N5c3RlbSIgfA0KICAgICAgICBPdXQtTnVsbA0KDQogICAgIyBLZWVwIHRoZSBib290c3RyYXAgcG9vbCBsb29wYmFjay1vbmx5LiBUaGUgY2x1c3RlciBtYW5hZ2VyIHdpbGwgbGF0ZXINCiAgICAjIHJlcGxhY2UgdGhlc2UgcnVsZXMgd2hlbiB0aGUgdXNlciBjcmVhdGVzIG9yIGpvaW5zIGEgcG9vbC4NCiAgICBHZXQtTmV0RmlyZXdhbGxSdWxlIGANCiAgICAgICAgLURpc3BsYXlOYW1lICJsb2NhbF93ZWJfbW9kdWxlX3N5c3RlbSBIVENvbmRvcioiIGANCiAgICAgICAgLUVycm9yQWN0aW9uIFNpbGVudGx5Q29udGludWUgfA0KICAgICAgICBSZW1vdmUtTmV0RmlyZXdhbGxSdWxlIGANCiAgICAgICAgICAgIC1FcnJvckFjdGlvbiBTaWxlbnRseUNvbnRpbnVlDQoNCiAgICBOZXctTmV0RmlyZXdhbGxSdWxlIGANCiAgICAgICAgLURpc3BsYXlOYW1lICJsb2NhbF93ZWJfbW9kdWxlX3N5c3RlbSBIVENvbmRvciBtYXN0ZXIiIGANCiAgICAgICAgLURpcmVjdGlvbiBJbmJvdW5kIGANCiAgICAgICAgLUFjdGlvbiBBbGxvdyBgDQogICAgICAgIC1Qcm9ncmFtICRtYXN0ZXJQYXRoIGANCiAgICAgICAgLVByb2ZpbGUgUHJpdmF0ZSBgDQogICAgICAgIC1SZW1vdGVBZGRyZXNzIExvY2FsU3VibmV0IHwNCiAgICAgICAgT3V0LU51bGwNCg0KICAgIFN0YXJ0LVNlcnZpY2UgLU5hbWUgJFNlcnZpY2VOYW1lDQoNCiAgICAoR2V0LVNlcnZpY2UgLU5hbWUgJFNlcnZpY2VOYW1lKS5XYWl0Rm9yU3RhdHVzKA0KICAgICAgICBbU3lzdGVtLlNlcnZpY2VQcm9jZXNzLlNlcnZpY2VDb250cm9sbGVyU3RhdHVzXTo6UnVubmluZywNCiAgICAgICAgW1RpbWVTcGFuXTo6RnJvbVNlY29uZHMoNDUpDQogICAgKQ0KDQogICAgJHNlcnZpY2UgPSBHZXQtU2VydmljZSAtTmFtZSAkU2VydmljZU5hbWUNCiAgICBpZiAoJHNlcnZpY2UuU3RhdHVzIC1uZSAiUnVubmluZyIpIHsNCiAgICAgICAgdGhyb3cgIlRoZSBDb25kb3Igc2VydmljZSBkaWQgbm90IHJlYWNoIHRoZSBSdW5uaW5nIHN0YXRlLiINCiAgICB9DQoNCiAgICAkdmVyc2lvbk91dHB1dCA9ICgNCiAgICAgICAgJiAkdmVyc2lvblBhdGggMj4mMSB8DQogICAgICAgIE91dC1TdHJpbmcNCiAgICApLlRyaW0oKQ0KDQogICAgaWYgKCRMQVNURVhJVENPREUgLW5lIDApIHsNCiAgICAgICAgdGhyb3cgImNvbmRvcl92ZXJzaW9uLmV4ZSBmYWlsZWQuIg0KICAgIH0NCg0KICAgIGlmICgNCiAgICAgICAgJEV4cGVjdGVkVmVyc2lvbiAtYW5kDQogICAgICAgICR2ZXJzaW9uT3V0cHV0IC1ub3RtYXRjaCBbcmVnZXhdOjpFc2NhcGUoJEV4cGVjdGVkVmVyc2lvbikNCiAgICApIHsNCiAgICAgICAgdGhyb3cgKA0KICAgICAgICAgICAgIkluc3RhbGxlZCB2ZXJzaW9uIGRvZXMgbm90IG1hdGNoIHRoZSBidW5kbGVkIHZlcnNpb24uICIgKw0KICAgICAgICAgICAgIkV4cGVjdGVkPSRFeHBlY3RlZFZlcnNpb24gT3V0cHV0PSR2ZXJzaW9uT3V0cHV0Ig0KICAgICAgICApDQogICAgfQ0KDQogICAgJHJlbGVhc2VPdXRwdXQgPSAoDQogICAgICAgICYgJGNvbmZpZ1ZhbFBhdGggIlJFTEVBU0VfRElSIiAyPiYxIHwNCiAgICAgICAgT3V0LVN0cmluZw0KICAgICkuVHJpbSgpDQoNCiAgICBpZiAoJExBU1RFWElUQ09ERSAtbmUgMCAtb3IgLW5vdCAkcmVsZWFzZU91dHB1dCkgew0KICAgICAgICB0aHJvdyAiY29uZG9yX2NvbmZpZ192YWwuZXhlIGNvdWxkIG5vdCByZWFkIHRoZSBuZXcgY29uZmlndXJhdGlvbi4iDQogICAgfQ0KDQogICAgJHJlc3VsdCA9IEB7DQogICAgICAgIHN1Y2Nlc3MgPSAkdHJ1ZQ0KICAgICAgICBzdGF0dXMgPSAibWFudWFsX2ZhbGxiYWNrX2luc3RhbGxlZCINCiAgICAgICAgbWVzc2FnZSA9ICgNCiAgICAgICAgICAgICJIVENvbmRvciB3YXMgaW5zdGFsbGVkIGJ5IHRoZSBvZmZpY2lhbCBtYW51YWwgV2luZG93cyAiICsNCiAgICAgICAgICAgICJpbnN0YWxsYXRpb24gbWV0aG9kIGFmdGVyIHRoZSBNU0kgTWFrZUFkbWluVG9rZW4gZmFpbHVyZS4iDQogICAgICAgICkNCiAgICAgICAgZXhwZWN0ZWRfdmVyc2lvbiA9ICRFeHBlY3RlZFZlcnNpb24NCiAgICAgICAgaW5zdGFsbGVkX3ZlcnNpb25fb3V0cHV0ID0gJHZlcnNpb25PdXRwdXQNCiAgICAgICAgaW5zdGFsbF9kaXIgPSAkSW5zdGFsbERpcg0KICAgICAgICBzZXJ2aWNlX25hbWUgPSAkU2VydmljZU5hbWUNCiAgICAgICAgc2VydmljZV9zdGF0dXMgPSBbc3RyaW5nXSRzZXJ2aWNlLlN0YXR1cw0KICAgICAgICBjb25maWdfcGF0aCA9ICRjb25maWdQYXRoDQogICAgICAgIGxvY2FsX2NvbmZpZ19wYXRoID0gJGxvY2FsQ29uZmlnUGF0aA0KICAgICAgICBleHRyYWN0aW9uX2RpciA9ICRTdGFnaW5nRGlyDQogICAgICAgIGV4dHJhY3Rpb25fbG9nID0gJEV4dHJhY3RMb2cNCiAgICAgICAgZmFpbGVkX21zaV9sb2cgPSAkRmFpbGVkTXNpTG9nDQogICAgICAgIGNvbXBsZXRlZF9hdCA9IFtEYXRlVGltZV06OlV0Y05vdy5Ub1N0cmluZygibyIpDQogICAgfQ0KDQogICAgU2F2ZS1SZXN1bHQgLURhdGEgJHJlc3VsdA0KICAgIGV4aXQgMA0KfQ0KY2F0Y2ggew0KICAgICRtYXN0ZXJMb2cgPSBKb2luLVBhdGggJEluc3RhbGxEaXIgImxvZ1xNYXN0ZXJMb2ciDQoNCiAgICAkcmVzdWx0ID0gQHsNCiAgICAgICAgc3VjY2VzcyA9ICRmYWxzZQ0KICAgICAgICBzdGF0dXMgPSAibWFudWFsX2ZhbGxiYWNrX2ZhaWxlZCINCiAgICAgICAgbWVzc2FnZSA9ICRfLkV4Y2VwdGlvbi5NZXNzYWdlDQogICAgICAgIGVycm9yID0gIiQoJF8uRXhjZXB0aW9uLkdldFR5cGUoKS5OYW1lKTogJCgkXy5FeGNlcHRpb24uTWVzc2FnZSkiDQogICAgICAgIGV4cGVjdGVkX3ZlcnNpb24gPSAkRXhwZWN0ZWRWZXJzaW9uDQogICAgICAgIGluc3RhbGxfZGlyID0gJEluc3RhbGxEaXINCiAgICAgICAgZXh0cmFjdGlvbl9kaXIgPSAkU3RhZ2luZ0Rpcg0KICAgICAgICBleHRyYWN0aW9uX2xvZyA9ICRFeHRyYWN0TG9nDQogICAgICAgIGZhaWxlZF9tc2lfbG9nID0gJEZhaWxlZE1zaUxvZw0KICAgICAgICBtYXN0ZXJfbG9nID0gJG1hc3RlckxvZw0KICAgICAgICBjb21wbGV0ZWRfYXQgPSBbRGF0ZVRpbWVdOjpVdGNOb3cuVG9TdHJpbmcoIm8iKQ0KICAgIH0NCg0KICAgIFNhdmUtUmVzdWx0IC1EYXRhICRyZXN1bHQNCiAgICBleGl0IDENCn0NCg=="
    $helperBytes = [System.Convert]::FromBase64String($helperBase64)
    [System.IO.File]::WriteAllBytes($helperPath, $helperBytes)


    $helperArguments = @(
        "-NoProfile"
        "-ExecutionPolicy"
        "Bypass"
        "-File"
        ('"{0}"' -f $helperPath)
        "-MsiPath"
        ('"{0}"' -f $HTCondorMsi)
        "-InstallDir"
        ('"{0}"' -f $HTCondorInstallDir)
        "-ServiceName"
        ('"{0}"' -f $HTCondorServiceName)
        "-StagingDir"
        ('"{0}"' -f $stagingDir)
        "-ExtractLog"
        ('"{0}"' -f $extractLog)
        "-ResultPath"
        ('"{0}"' -f $manualResultPath)
        "-ExpectedVersion"
        ('"{0}"' -f [string]$BundleInfo.product_version)
        "-FailedMsiLog"
        ('"{0}"' -f $FailedMsiLog)
    )

    Write-Info (
        "A second administrator approval is required for the " +
        "manual installation fallback."
    )

    try {
        $helperProcess = Start-Process `
            -FilePath "powershell.exe" `
            -Verb RunAs `
            -ArgumentList $helperArguments `
            -Wait `
            -PassThru
    }
    catch {
        throw (
            "The manual HTCondor installation could not be started: " +
            $_.Exception.Message
        )
    }

    $manualResult = $null

    if (Test-Path -LiteralPath $manualResultPath -PathType Leaf) {
        try {
            $manualResult = Get-Content `
                -LiteralPath $manualResultPath `
                -Raw `
                -Encoding UTF8 |
                ConvertFrom-Json
        }
        catch {}
    }

    if ($helperProcess.ExitCode -ne 0) {
        $message = if ($manualResult -and $manualResult.message) {
            [string]$manualResult.message
        }
        else {
            "The manual HTCondor installation fallback failed."
        }

        throw (
            "$message Extraction log: $extractLog"
        )
    }

    $runtime = Get-HTCondorRuntimeStatus

    if (-not $runtime.installed) {
        throw (
            "The manual installation completed, but HTCondor " +
            "runtime validation failed."
        )
    }

    $finalResult = @{
        success = $true
        status = "manual_fallback_installed"
        message = (
            "HTCondor was installed successfully by the manual " +
            "Windows installation fallback."
        )
        expected_version = [string]$BundleInfo.product_version
        installed_version_output = [string]$runtime.version_output
        install_dir = $HTCondorInstallDir
        service_name = $HTCondorServiceName
        service_status = [string]$runtime.service_status
        failed_msi_log = $FailedMsiLog
        extraction_log = $extractLog
        helper_result = $manualResult
        completed_at = [DateTime]::UtcNow.ToString("o")
    }

    Write-JsonFile `
        -Path $HTCondorInstallResult `
        -Value $finalResult

    Write-Ok "HTCondor manual installation completed."
    Write-Info "Version: $($runtime.version_output)"
    Write-Info "Service: $($runtime.service_status)"
}

function Install-HTCondor {
    param(
        [Parameter(Mandatory = $true)]
        $BundleInfo
    )

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $HTCondorRuntimeDir |
        Out-Null

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $HTCondorLogDir |
        Out-Null

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $logPath = Join-Path `
        $HTCondorLogDir `
        "htcondor_install_$timestamp.log"

    # The bootstrap installation intentionally does not run or submit jobs.
    # The administrator UI will later assign the real role:
    # Central Manager + Submit, or Execute.
    $msiArguments = @(
        "/i"
        ('"{0}"' -f $HTCondorMsi)
        "/qn"
        "/norestart"
        "/L*v"
        ('"{0}"' -f $logPath)
        'NEWPOOL="Y"'
        ('POOLNAME="{0}"' -f $HTCondorBootstrapPoolName)
        'RUNJOBS="N"'
        'VACATEJOBS="Y"'
        'SUBMITJOBS="N"'
        'CONDOREMAIL=""'
        'SMTPSERVER=""'
        'ALLOWREAD="$(IP_ADDRESS)"'
        'ALLOWWRITE="$(IP_ADDRESS)"'
        'ALLOWADMINISTRATOR="$(IP_ADDRESS)"'
        ('INSTALLDIR="{0}"' -f $HTCondorInstallDir)
        'POOLHOSTNAME="$(IP_ADDRESS)"'
        'ACCOUNTINGDOMAIN="none"'
        'JVMLOCATION=""'
        'USEVMUNIVERSE="N"'
        'VMMEMORY="128"'
        'VMMAXNUMBER="$(NUM_CPUS)"'
        'VMNETWORKING="N"'
    )

    Write-Info (
        "Windows administrator approval is required once " +
        "to install the Condor service."
    )
    Write-Info "Installer log: $logPath"

    try {
        $process = Start-Process `
            -FilePath "msiexec.exe" `
            -Verb RunAs `
            -ArgumentList $msiArguments `
            -Wait `
            -PassThru
    }
    catch {
        $result = @{
            success = $false
            status = "elevation_or_install_start_failed"
            message = (
                "HTCondor installation was not started. " +
                "Administrator approval may have been cancelled."
            )
            error = "$($_.Exception.GetType().Name): $($_.Exception.Message)"
            expected_version = [string]$BundleInfo.product_version
            log_path = $logPath
            completed_at = [DateTime]::UtcNow.ToString("o")
        }
        Write-JsonFile -Path $HTCondorInstallResult -Value $result
        throw $result.message
    }

    $exitCode = [int]$process.ExitCode
    $successExitCodes = @(0, 1641, 3010)

    if ($successExitCodes -notcontains $exitCode) {
        $result = @{
            success = $false
            status = "msi_failed_manual_fallback_starting"
            message = (
                "HTCondor MSI installation failed with exit code " +
                "$exitCode. The manual Windows installation fallback " +
                "will now be used."
            )
            msi_exit_code = $exitCode
            expected_version = [string]$BundleInfo.product_version
            log_path = $logPath
            completed_at = [DateTime]::UtcNow.ToString("o")
        }

        Write-JsonFile -Path $HTCondorInstallResult -Value $result
        Write-Warn $result.message

        Install-HTCondorManualFallback `
            -BundleInfo $BundleInfo `
            -FailedMsiLog $logPath

        return
    }

    $deadline = (Get-Date).AddSeconds(45)
    $runtime = Get-HTCondorRuntimeStatus

    while (
        -not $runtime.installed -and
        (Get-Date) -lt $deadline
    ) {
        Start-Sleep -Seconds 2
        $runtime = Get-HTCondorRuntimeStatus
    }

    if (-not $runtime.installed) {
        $result = @{
            success = $false
            status = "post_install_validation_failed"
            message = (
                "The MSI completed, but the Condor service or " +
                "condor_version.exe could not be validated."
            )
            msi_exit_code = $exitCode
            runtime = $runtime
            expected_version = [string]$BundleInfo.product_version
            log_path = $logPath
            completed_at = [DateTime]::UtcNow.ToString("o")
        }
        Write-JsonFile -Path $HTCondorInstallResult -Value $result
        throw $result.message
    }

    if (
        $runtime.service_status -ne "Running" -and
        $runtime.service_exists
    ) {
        try {
            Start-Service -Name $HTCondorServiceName
            (Get-Service -Name $HTCondorServiceName).WaitForStatus(
                [System.ServiceProcess.ServiceControllerStatus]::Running,
                [TimeSpan]::FromSeconds(30)
            )
        }
        catch {
            Write-Warn (
                "HTCondor is installed, but the Condor service " +
                "could not be started automatically."
            )
        }

        $runtime = Get-HTCondorRuntimeStatus
    }

    $result = @{
        success = $true
        status = "installed"
        message = (
            "HTCondor was installed in bootstrap mode. " +
            "No production cluster role has been assigned yet."
        )
        expected_version = [string]$BundleInfo.product_version
        installed_version_output = [string]$runtime.version_output
        msi_exit_code = $exitCode
        reboot_required = [bool]($exitCode -in @(1641, 3010))
        install_dir = $HTCondorInstallDir
        service_name = $HTCondorServiceName
        service_status = [string]$runtime.service_status
        version_exe = [string]$runtime.version_exe
        log_path = $logPath
        completed_at = [DateTime]::UtcNow.ToString("o")
    }

    Write-JsonFile -Path $HTCondorInstallResult -Value $result
    Write-Ok "HTCondor installation completed."
    Write-Info "Version: $($runtime.version_output)"
    Write-Info "Service: $($runtime.service_status)"

    if ($result.reboot_required) {
        Write-Warn (
            "Windows reported that a restart is required " +
            "before HTCondor is fully available."
        )
    }
}

function Ensure-HTCondorInstalled {
    if ($SkipHTCondorInstall) {
        Write-Warn "HTCondor installation check was skipped by parameter."
        return
    }

    Write-Step "Install and validate bundled HTCondor"

    # Fast path: normal launches should not request UAC again after the
    # machine has already passed the one-click installation and smoke test.
    if (
        Test-Path `
            -LiteralPath $HTCondorInstallResult `
            -PathType Leaf
    ) {
        try {
            $savedResult = Get-Content `
                -LiteralPath $HTCondorInstallResult `
                -Raw `
                -Encoding UTF8 |
                ConvertFrom-Json

            $savedRuntime = Get-HTCondorRuntimeStatus

            if (
                $savedResult.success -and
                [string]$savedResult.status -eq "fully_validated" -and
                $savedRuntime.installed -and
                [string]$savedRuntime.service_status -eq "Running"
            ) {
                if (Test-HTCondorSecretReadable) {
                    Write-Ok "HTCondor is already fully validated."
                    Write-Info "Machine: $($savedResult.machine)"
                    Write-Info "Version: $($savedResult.htcondor_version)"
                    Write-Info "Service: $($savedRuntime.service_status)"
                    Add-HTCondorBinToProcessPath
                    return
                }

                Write-Warn (
                    "HTCondor 已通过自检，但后端当前用户没有读取提交密文的权限。" +
                    "启动器将自动请求管理员授权并修复 ACL。"
                )
            }
        }
        catch {
            Write-Warn (
                "The saved HTCondor validation result could not be used. " +
                "Repair mode will run."
            )
        }
    }

    if (
        -not (
            Test-Path `
                -LiteralPath $HTCondorOneClickSetup `
                -PathType Leaf
        )
    ) {
        throw (
            "Missing HTCondor one-click setup script: " +
            $HTCondorOneClickSetup
        )
    }

    $currentAclUser = Get-CurrentUserForHTCondorAcl

    $arguments = @(
        "-NoProfile"
        "-ExecutionPolicy"
        "Bypass"
        "-File"
        ('"{0}"' -f $HTCondorOneClickSetup)
        "-ProjectRoot"
        ('"{0}"' -f $ProjectRoot)
        "-BackendUserName"
        ('"{0}"' -f [string]$currentAclUser.name)
        "-BackendUserSid"
        ('"{0}"' -f [string]$currentAclUser.sid)
    )

    Write-Info (
        "The setup will request administrator approval only when " +
        "installation or repair is needed."
    )

    $process = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $arguments `
        -Wait `
        -PassThru

    if ($process.ExitCode -ne 0) {
        $message = "HTCondor one-click setup failed."

        if (
            Test-Path `
                -LiteralPath $HTCondorInstallResult `
                -PathType Leaf
        ) {
            try {
                $result = Get-Content `
                    -LiteralPath $HTCondorInstallResult `
                    -Raw `
                    -Encoding UTF8 |
                    ConvertFrom-Json

                if ($result.message) {
                    $message = [string]$result.message
                }
            }
            catch {}
        }

        throw $message
    }

    if (
        -not (
            Test-Path `
                -LiteralPath $HTCondorInstallResult `
                -PathType Leaf
        )
    ) {
        throw (
            "HTCondor setup completed without install_result.json."
        )
    }

    $installResult = Get-Content `
        -LiteralPath $HTCondorInstallResult `
        -Raw `
        -Encoding UTF8 |
        ConvertFrom-Json

    if (
        -not $installResult.success -or
        [string]$installResult.status -ne "fully_validated"
    ) {
        throw (
            "HTCondor setup did not reach fully_validated status. " +
            "Current status: $($installResult.status)"
        )
    }

    Write-Ok "HTCondor is fully installed and validated."
    Write-Info "Machine: $($installResult.machine)"
    Write-Info "Version: $($installResult.htcondor_version)"
    Write-Info "Service: $($installResult.service_status)"
    Write-Info (
        "Smoke test ClusterId: " +
        $installResult.smoke_test.cluster_id
    )

    Add-HTCondorBinToProcessPath
}

# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------

function Test-PythonExact(
    [string]$PythonExe,
    [string]$ExpectedVersion
) {
    if (
        -not $PythonExe -or
        -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)
    ) {
        return $false
    }

    try {
        $version = (
            & $PythonExe `
                -c "import platform; print(platform.python_version())" `
                2>$null
        ).Trim()

        return $version -eq $ExpectedVersion
    }
    catch {
        return $false
    }
}

function Get-PythonVersion([string]$PythonExe) {
    try {
        return (
            & $PythonExe `
                -c "import platform; print(platform.python_version())"
        ).Trim()
    }
    catch {
        return ""
    }
}

function Find-ExactPython([string]$ExpectedVersion) {
    $candidates = New-Object System.Collections.Generic.List[string]

    try {
        $found = (
            & py -3.12 `
                -c "import sys; print(sys.executable)" `
                2>$null
        ).Trim()

        if ($found) {
            [void]$candidates.Add($found)
        }
    }
    catch {}

    foreach ($candidate in @(
        "D:\develop\python312\python.exe",
        "D:\Anaconda\python.exe",
        "D:\ProgramData\anaconda3\python.exe",
        "C:\ProgramData\Anaconda3\python.exe",
        "$env:USERPROFILE\anaconda3\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "C:\Python312\python.exe"
    )) {
        if ($candidate) {
            [void]$candidates.Add($candidate)
        }
    }

    try {
        $pathPython = (
            Get-Command python.exe -ErrorAction Stop
        ).Source

        if ($pathPython) {
            [void]$candidates.Add($pathPython)
        }
    }
    catch {}

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-PythonExact $candidate $ExpectedVersion) {
            return $candidate
        }
    }

    return ""
}

function Find-CondaExe {
    $candidates = @(
        "D:\Anaconda\Scripts\conda.exe",
        "D:\ProgramData\anaconda3\Scripts\conda.exe",
        "C:\ProgramData\Anaconda3\Scripts\conda.exe",
        "$env:USERPROFILE\anaconda3\Scripts\conda.exe",
        "$env:USERPROFILE\miniconda3\Scripts\conda.exe"
    )

    try {
        $path = (
            Get-Command conda.exe -ErrorAction Stop
        ).Source

        if ($path) {
            $candidates = @($path) + $candidates
        }
    }
    catch {}

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (
            $candidate -and
            (Test-Path -LiteralPath $candidate -PathType Leaf)
        ) {
            return $candidate
        }
    }

    return ""
}

function Get-VenvPython {
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    $condaPython = Join-Path $VenvDir "python.exe"

    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        return $venvPython
    }

    if (Test-Path -LiteralPath $condaPython -PathType Leaf) {
        return $condaPython
    }

    return ""
}

function Backup-ExistingEnvironment {
    if (-not (Test-Path -LiteralPath $VenvDir)) {
        return ""
    }

    $backup = (
        "${VenvDir}_backup_" +
        (Get-Date -Format "yyyyMMdd_HHmmss")
    )

    Write-Info "Moving old environment to: $backup"
    Move-Item `
        -LiteralPath $VenvDir `
        -Destination $backup `
        -Force

    return $backup
}

function New-ProjectEnvironment {
    Write-Step "Create backend\.venv with Python $TargetPythonVersion"

    $basePython = Find-ExactPython $TargetPythonVersion

    if ($basePython) {
        Write-Info "Found Python: $basePython"
        & $basePython -m venv $VenvDir

        if ($LASTEXITCODE -ne 0) {
            throw "python -m venv failed."
        }

        return
    }

    $conda = Find-CondaExe

    if ($conda) {
        Write-Info (
            "Exact Python was not found. Conda will create " +
            "Python $TargetPythonVersion in backend\.venv."
        )
        Write-Info "Conda: $conda"

        & $conda create `
            --prefix $VenvDir `
            -c defaults `
            "python=$TargetPythonVersion" `
            pip `
            -y

        if ($LASTEXITCODE -ne 0) {
            throw "Conda environment creation failed."
        }

        return
    }

    throw (
        "Python $TargetPythonVersion or Anaconda/Miniconda was not found. " +
        "Install one and run start_system.bat again."
    )
}

function Test-EnvironmentReady(
    [string]$PythonExe,
    [string]$LockHash
) {
    if (-not (Test-PythonExact $PythonExe $TargetPythonVersion)) {
        return $false
    }

    if (-not (Test-Path -LiteralPath $FingerprintFile -PathType Leaf)) {
        return $false
    }

    try {
        $fingerprint = Get-Content `
            -LiteralPath $FingerprintFile `
            -Raw `
            -Encoding UTF8 |
            ConvertFrom-Json

        if ([string]$fingerprint.lock_sha256 -ne $LockHash) {
            return $false
        }

        & $PythonExe `
            $VerifyScript `
            --lock $LockFile `
            --quiet

        if ($LASTEXITCODE -ne 0) {
            return $false
        }

        & $PythonExe -m pip check *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Ensure-PythonEnvironment {
    foreach ($required in @(
        $LockFile,
        $VerifyScript,
        $DetectScript
    )) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Missing required file: $required"
        }
    }

    $lockHash = (
        Get-FileHash `
            -LiteralPath $LockFile `
            -Algorithm SHA256
    ).Hash.ToLowerInvariant()

    $existingPython = Get-VenvPython
    $ready = $false

    if (-not $RebuildEnvironment -and $existingPython) {
        Write-Step "Check existing backend\.venv"
        Write-Info "Python: $existingPython"
        Write-Info "Version: $(Get-PythonVersion $existingPython)"

        $ready = Test-EnvironmentReady `
            $existingPython `
            $lockHash

        if ($ready) {
            Write-Ok "Existing Python environment is ready."
        }
    }

    $backupPath = ""

    if (-not $ready) {
        $backupPath = Backup-ExistingEnvironment

        try {
            New-ProjectEnvironment

            $venvPython = Get-VenvPython

            if (-not $venvPython) {
                throw "python.exe was not found in backend\.venv."
            }

            if (
                -not (
                    Test-PythonExact `
                        $venvPython `
                        $TargetPythonVersion
                )
            ) {
                throw (
                    "backend\.venv does not use Python " +
                    $TargetPythonVersion
                )
            }

            Write-Step "Install locked backend dependencies"

            & $venvPython `
                -m pip install `
                --disable-pip-version-check `
                --upgrade `
                "pip==26.1.2"

            if ($LASTEXITCODE -ne 0) {
                throw "pip upgrade failed."
            }

            & $venvPython `
                -m pip install `
                --disable-pip-version-check `
                --no-cache-dir `
                -r $LockFile

            if ($LASTEXITCODE -ne 0) {
                throw "Dependency installation failed."
            }

            Write-Step "Verify backend environment"

            & $venvPython `
                $VerifyScript `
                --lock $LockFile

            if ($LASTEXITCODE -ne 0) {
                throw "Version verification failed."
            }

            & $venvPython -m pip check

            if ($LASTEXITCODE -ne 0) {
                throw "pip check found dependency conflicts."
            }

            [ordered]@{
                python_version = $TargetPythonVersion
                lock_sha256 = $lockHash
                created_at = (Get-Date).ToString("s")
            } |
                ConvertTo-Json |
                Set-Content `
                    -LiteralPath $FingerprintFile `
                    -Encoding UTF8

            Write-Ok "backend\.venv is ready."
        }
        catch {
            Write-Host (
                "[ERROR] Environment setup failed: " +
                $_.Exception.Message
            ) -ForegroundColor Red

            if (Test-Path -LiteralPath $VenvDir) {
                Remove-Item `
                    -LiteralPath $VenvDir `
                    -Recurse `
                    -Force `
                    -ErrorAction SilentlyContinue
            }

            if (
                $backupPath -and
                (Test-Path -LiteralPath $backupPath)
            ) {
                Write-Info "Restoring previous environment."
                Move-Item `
                    -LiteralPath $backupPath `
                    -Destination $VenvDir `
                    -Force
            }

            throw
        }
    }

    $venvPython = Get-VenvPython

    if (-not $venvPython) {
        throw "backend\.venv is missing python.exe."
    }

    return $venvPython
}

# ---------------------------------------------------------------------------
# Frontend dependency and build management
# ---------------------------------------------------------------------------

function Find-NpmCommand {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue

    if (-not $npm) {
        $npm = Get-Command npm -ErrorAction SilentlyContinue
    }

    if (-not $npm) {
        throw "npm was not found. Install Node.js and run the launcher again."
    }

    return $npm.Source
}

function Get-FrontendPackageHash {
    $packageLock = Join-Path $FrontendDir "package-lock.json"
    $packageJson = Join-Path $FrontendDir "package.json"

    if (Test-Path -LiteralPath $packageLock -PathType Leaf) {
        return (
            Get-FileHash `
                -LiteralPath $packageLock `
                -Algorithm SHA256
        ).Hash.ToLowerInvariant()
    }

    if (Test-Path -LiteralPath $packageJson -PathType Leaf) {
        return (
            Get-FileHash `
                -LiteralPath $packageJson `
                -Algorithm SHA256
        ).Hash.ToLowerInvariant()
    }

    throw "frontend\package.json was not found."
}

function Ensure-FrontendDependencies {
    param(
        [Parameter(Mandatory = $true)]
        [string]$NpmCommand
    )

    $nodeModules = Join-Path $FrontendDir "node_modules"
    $dependencyFingerprint = Join-Path `
        $FrontendDir `
        "node_modules\.local_web_package_hash"

    $packageHash = Get-FrontendPackageHash
    $storedHash = ""

    if (
        Test-Path `
            -LiteralPath $dependencyFingerprint `
            -PathType Leaf
    ) {
        $storedHash = (
            Get-Content `
                -LiteralPath $dependencyFingerprint `
                -Raw
        ).Trim().ToLowerInvariant()
    }

    $installRequired = [bool](
        $ForceFrontendInstall -or
        -not (
            Test-Path `
                -LiteralPath $nodeModules `
                -PathType Container
        ) -or
        $storedHash -ne $packageHash
    )

    if (-not $installRequired) {
        Write-Ok "Frontend dependencies are current."
        return
    }

    Write-Step "Install frontend dependencies"

    Push-Location $FrontendDir

    try {
        $packageLock = Join-Path $FrontendDir "package-lock.json"

        if (
            Test-Path `
                -LiteralPath $packageLock `
                -PathType Leaf
        ) {
            & $NpmCommand ci

            if ($LASTEXITCODE -ne 0) {
                Write-Warn "npm ci failed; falling back to npm install."
                & $NpmCommand install
            }
        }
        else {
            & $NpmCommand install
        }

        if ($LASTEXITCODE -ne 0) {
            throw "Frontend dependency installation failed."
        }

        New-Item `
            -ItemType Directory `
            -Force `
            -Path $nodeModules |
            Out-Null

        Set-Content `
            -LiteralPath $dependencyFingerprint `
            -Value $packageHash `
            -Encoding ASCII

        Write-Ok "Frontend dependencies are ready."
    }
    finally {
        Pop-Location
    }
}

function Test-FrontendBuildRequired {
    $distIndex = Join-Path $FrontendDir "dist\index.html"

    if (-not (Test-Path -LiteralPath $distIndex -PathType Leaf)) {
        return $true
    }

    $distTime = (
        Get-Item -LiteralPath $distIndex
    ).LastWriteTimeUtc

    $sourceCandidates = New-Object System.Collections.Generic.List[string]

    foreach ($path in @(
        (Join-Path $FrontendDir "src"),
        (Join-Path $FrontendDir "index.html"),
        (Join-Path $FrontendDir "package.json"),
        (Join-Path $FrontendDir "package-lock.json"),
        (Join-Path $FrontendDir "vite.config.js"),
        (Join-Path $FrontendDir "vite.config.mjs"),
        (Join-Path $FrontendDir "vite.config.ts")
    )) {
        if (Test-Path -LiteralPath $path) {
            [void]$sourceCandidates.Add($path)
        }
    }

    foreach ($path in $sourceCandidates) {
        if (Test-Path -LiteralPath $path -PathType Container) {
            $newerFile = Get-ChildItem `
                -LiteralPath $path `
                -Recurse `
                -File `
                -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.LastWriteTimeUtc -gt $distTime
                } |
                Select-Object -First 1

            if ($newerFile) {
                return $true
            }
        }
        else {
            if (
                (Get-Item -LiteralPath $path).LastWriteTimeUtc `
                    -gt $distTime
            ) {
                return $true
            }
        }
    }

    return $false
}

function Build-Frontend {
    param(
        [switch]$AlwaysBuild
    )

    if (-not (Test-Path -LiteralPath $FrontendDir -PathType Container)) {
        throw "Frontend directory was not found: $FrontendDir"
    }

    $npm = Find-NpmCommand
    $buildRequired = [bool](
        $AlwaysBuild -or
        (Test-FrontendBuildRequired)
    )

    if (-not $buildRequired) {
        Write-Ok "Frontend dist is current; build skipped."
        return
    }

    Ensure-FrontendDependencies -NpmCommand $npm

    Write-Step "Build frontend"

    Push-Location $FrontendDir

    try {
        & $npm run build

        if ($LASTEXITCODE -ne 0) {
            throw "npm run build failed."
        }
    }
    finally {
        Pop-Location
    }

    $distIndex = Join-Path $FrontendDir "dist\index.html"

    if (-not (Test-Path -LiteralPath $distIndex -PathType Leaf)) {
        throw "Frontend build finished without creating dist\index.html."
    }

    Write-Ok "Frontend build completed."
}

# ---------------------------------------------------------------------------
# Backend runtime environment and resource detection
# ---------------------------------------------------------------------------

function Set-BackendRuntimeEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$VenvPython
    )

    $venvScripts = Join-Path $VenvDir "Scripts"
    $env:PATH = "$VenvDir;$venvScripts;$env:PATH"

    Set-DefaultProcessEnvironment "PYTHONIOENCODING" "utf-8"
    Set-DefaultProcessEnvironment "PYTHONUTF8" "1"
    Set-DefaultProcessEnvironment "PYTHONUNBUFFERED" "1"

    # Keep Dask settings available until the HTCondor migration is complete.
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_DASK_WORKER_PORTS" `
        "9000:9099"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_DASK_NANNY_PORTS" `
        "9100:9199"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_DASK_WORKER_PORTS_FIREWALL" `
        "9000-9099"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_DASK_NANNY_PORTS_FIREWALL" `
        "9100-9199"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_DASK_RESULT_TIMEOUT_SECONDS" `
        "180"

    # Scheduler defaults. Existing process-level values may override them.
    Set-DefaultProcessEnvironment "LOCAL_WEB_CPU_AFFINITY" "1"
    Set-DefaultProcessEnvironment "LOCAL_WEB_RESERVED_CORES" "2"
    Set-DefaultProcessEnvironment "LOCAL_WEB_CORES_PER_PROCESS" "5"
    Set-DefaultProcessEnvironment "LOCAL_WEB_MEMORY_PER_WORKER_GB" "3"

    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_MODEL_REUSE_LARGE_RESOURCE_GROUP_CAP_GB" `
        "2.0"

    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_PARALLEL_PROGRESS_SCAN_SECONDS" `
        "1.0"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_PARALLEL_MONITOR_LOG_SECONDS" `
        "20"

    Set-DefaultProcessEnvironment "LOCAL_WEB_DYNAMIC_WORKER_BOOST" "1"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_DYNAMIC_WORKER_BOOST_EXTRA" `
        "1"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_DYNAMIC_WORKER_BOOST_CPU_BELOW" `
        "55"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_DYNAMIC_WORKER_BOOST_MEMORY_BELOW" `
        "88"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_DYNAMIC_WORKER_BOOST_MIN_MEMORY_GB" `
        "2.0"

    Set-DefaultProcessEnvironment "LOCAL_WEB_CPU_QUEUE_THRESHOLD" "99"
    Set-DefaultProcessEnvironment "LOCAL_WEB_UTIL_SCHEDULER" "1"
    Set-DefaultProcessEnvironment "LOCAL_WEB_UTIL_CPU_LOW" "60"
    Set-DefaultProcessEnvironment "LOCAL_WEB_UTIL_CPU_HIGH" "92"
    Set-DefaultProcessEnvironment "LOCAL_WEB_UTIL_MEMORY_SOFT" "92"
    Set-DefaultProcessEnvironment "LOCAL_WEB_UTIL_MEMORY_HARD" "98"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_UTIL_IO_READ_SOFT_MB_S" `
        "120"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_UTIL_IO_READ_HARD_MB_S" `
        "400"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_UTIL_IO_WRITE_SOFT_MB_S" `
        "60"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_UTIL_IO_WRITE_HARD_MB_S" `
        "300"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_UTIL_SCALE_UP_SAMPLES" `
        "2"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_UTIL_SCALE_UP_COOLDOWN_SECONDS" `
        "8"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_UTIL_SCALE_DOWN_COOLDOWN_SECONDS" `
        "5"

    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_IO_CPU_LOW_THRESHOLD" `
        "55"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_IO_MEMORY_THRESHOLD" `
        "95"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_IO_MIN_AVAILABLE_MEMORY_GB" `
        "0.8"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_IO_READ_MB_S_THRESHOLD" `
        "120"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_IO_WRITE_MB_S_THRESHOLD" `
        "60"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_IO_DISK_BUSY_THRESHOLD" `
        "70"

    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_CHILD_START_STAGGER_SECONDS" `
        "1"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_CHILD_START_WAIT_SECONDS" `
        "1"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_CHILD_START_CPU_THRESHOLD" `
        "99"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_CHILD_START_MEMORY_THRESHOLD" `
        "99"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_CHILD_START_MIN_MEMORY_GB" `
        "0.8"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_CHILD_COLD_START_MIN_MEMORY_GB" `
        "0.6"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_CHILD_COLD_START_MEMORY_THRESHOLD" `
        "99.8"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_CHILD_START_MIN_DISK_FREE_GB" `
        "0.5"

    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_ADAPTIVE_CHILD_START" `
        "1"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_ADAPTIVE_CHILD_START_MIN_SECONDS" `
        "1"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_ADAPTIVE_CHILD_START_MAX_SECONDS" `
        "8"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_ADAPTIVE_CHILD_START_SAMPLE_SECONDS" `
        "1.5"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_ADAPTIVE_CHILD_START_CPU_DECLINE" `
        "10"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_ADAPTIVE_CHILD_START_STABLE_SAMPLES" `
        "3"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_ADAPTIVE_CHILD_START_MAX_PROBE_SECONDS" `
        "10"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_ADAPTIVE_CHILD_START_MIN_PEAK_CPU" `
        "60"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_ADAPTIVE_CHILD_START_MEMORY_THRESHOLD" `
        "99"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_ADAPTIVE_CHILD_START_MIN_MEMORY_GB" `
        "0.5"

    Set-DefaultProcessEnvironment "OPENBLAS_NUM_THREADS" "1"
    Set-DefaultProcessEnvironment "OMP_NUM_THREADS" "1"
    Set-DefaultProcessEnvironment "MKL_NUM_THREADS" "1"
    Set-DefaultProcessEnvironment "GOTO_NUM_THREADS" "1"
    Set-DefaultProcessEnvironment "NUMEXPR_NUM_THREADS" "1"

    $runtimeDir = Join-Path $BackendDir "runtime_user"
    $pyiTemp = Join-Path $BackendDir ".pyi_tmp"

    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_RUNTIME_DIR" `
        $runtimeDir
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_INPUT_LINK_ORDER" `
        "hardlink"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_ALLOW_INPUT_HARDLINKS" `
        "1"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_ALLOW_INPUT_SYMLINKS" `
        "0"
    Set-DefaultProcessEnvironment `
        "LOCAL_WEB_INPUT_LINK_FALLBACK" `
        "error"

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $env:LOCAL_WEB_RUNTIME_DIR |
        Out-Null

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $pyiTemp |
        Out-Null

    $env:TMP = $pyiTemp
    $env:TEMP = $pyiTemp
    $env:TMPDIR = $pyiTemp

    Get-ChildItem `
        -LiteralPath $pyiTemp `
        -Directory `
        -Filter "_MEI*" `
        -ErrorAction SilentlyContinue |
        Remove-Item `
            -Recurse `
            -Force `
            -ErrorAction SilentlyContinue

    Write-Step "Detect local resources"

    $resourcesJson = (
        & $VenvPython $DetectScript --json |
        Out-String
    ).Trim()

    if ($LASTEXITCODE -ne 0) {
        throw "detect_resources.py failed."
    }

    $resources = $resourcesJson | ConvertFrom-Json

    $env:LOCAL_WEB_DETECTED_CPU_COUNT = [string]$resources.cpu_count
    $env:LOCAL_WEB_DETECTED_MEMORY_GB = [string]$resources.memory_gb
    $env:LOCAL_WEB_SUGGESTED_PROCESS_SLOTS = (
        [string]$resources.suggested_process_slots
    )
    $env:LOCAL_WEB_MAX_PROCESS_SLOTS = (
        [string]$resources.max_process_slots
    )
    $env:LOCAL_WEB_TOTAL_COMPUTE_THREADS = (
        [string]$resources.total_compute_threads
    )
    $env:LOCAL_WEB_MAX_THREADS_PER_CHILD = (
        [string]$resources.max_threads_per_child
    )

    Write-Info "CPU cores: $($resources.cpu_count)"
    Write-Info "Memory GB: $($resources.memory_gb)"
    Write-Info (
        "Suggested process slots: " +
        $resources.suggested_process_slots
    )
    Write-Info "Maximum process slots: $($resources.max_process_slots)"
    Write-Info (
        "Total compute threads: " +
        $resources.total_compute_threads
    )
    Write-Info (
        "Maximum threads per child: " +
        $resources.max_threads_per_child
    )
}

function Start-BackendServer {
    param(
        [Parameter(Mandatory = $true)]
        [string]$VenvPython
    )

    Write-Step "Validate backend import"

    Push-Location $BackendDir

    try {
        & $VenvPython `
            -c "import app.main; print('[OK] app.main import succeeded')"

        if ($LASTEXITCODE -ne 0) {
            throw "app.main import failed."
        }

        Write-Step "Start local_web_module_system backend"
        Write-Info "Python: $VenvPython"
        Write-Info "URL: $SystemUrl"

        if (Test-SystemServerReady) {
            Write-Warn "System is already running at $SystemUrl; reusing the existing server."
            if (-not $NoBrowser) {
                Start-Process $SystemUrl
            }
            return 0
        }

        if (-not $NoBrowser) {
            Start-Process $SystemUrl
        }

        & $VenvPython `
            -m uvicorn `
            app.main:app `
            --host 127.0.0.1 `
            --port 8000

        return $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

# ---------------------------------------------------------------------------
# Main mode dispatcher
# ---------------------------------------------------------------------------

try {
    Write-Host ""
    Write-Host "local_web_module_system launcher"
    Write-Info "Mode: $Mode"
    Write-Info "Project root: $ProjectRoot"

    if ($Mode -eq "Frontend") {
        Build-Frontend -AlwaysBuild
        exit 0
    }

    Ensure-HTCondorInstalled
$venvPythonOutput = @(Ensure-PythonEnvironment)

$venvPython = $venvPythonOutput |
    Where-Object {
        $_ -is [string] -and
        $_.Trim() -and
        (Test-Path -LiteralPath $_.Trim() -PathType Leaf) -and
        ($_.Trim().ToLower().EndsWith("python.exe"))
    } |
    Select-Object -Last 1

$venvPython = [string]$venvPython

if (-not $venvPython) {
    throw "Ensure-PythonEnvironment did not return a valid python.exe path."
}
    Add-HTCondorBinToProcessPath
    Set-BackendRuntimeEnvironment -VenvPython $venvPython

    if (
        $Mode -eq "System" -and
        -not $SkipFrontendBuild
    ) {
        Build-Frontend
    }

    $serverExitCode = Start-BackendServer -VenvPython $venvPython
    exit $serverExitCode
}
catch {
    Write-Host ""
    Write-Host (
        "[ERROR] " +
        $_.Exception.Message
    ) -ForegroundColor Red

    exit 1
}

