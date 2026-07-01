param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [Parameter(Mandatory = $true)]
    [string]$MsiSource
)

$ErrorActionPreference = "Stop"

function Get-MsiProperty {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Property
    )

    $installer = New-Object -ComObject WindowsInstaller.Installer
    try {
        $database = $installer.GetType().InvokeMember(
            "OpenDatabase",
            "InvokeMethod",
            $null,
            $installer,
            @($Path, 0)
        )

        $query = "SELECT ``Value`` FROM ``Property`` WHERE ``Property``='$Property'"
        $view = $database.GetType().InvokeMember(
            "OpenView",
            "InvokeMethod",
            $null,
            $database,
            @($query)
        )

        $view.GetType().InvokeMember(
            "Execute",
            "InvokeMethod",
            $null,
            $view,
            $null
        ) | Out-Null

        $record = $view.GetType().InvokeMember(
            "Fetch",
            "InvokeMethod",
            $null,
            $view,
            $null
        )

        if ($null -eq $record) {
            return ""
        }

        return $record.StringData(1)
    }
    finally {
        if ($null -ne $installer) {
            [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer) | Out-Null
        }
    }
}

$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$MsiSource = [System.IO.Path]::GetFullPath($MsiSource)

if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "项目根目录不存在：$ProjectRoot"
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "backend") -PathType Container)) {
    throw "项目根目录下没有 backend 文件夹：$ProjectRoot"
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "frontend") -PathType Container)) {
    throw "项目根目录下没有 frontend 文件夹：$ProjectRoot"
}

if (-not (Test-Path -LiteralPath $MsiSource -PathType Leaf)) {
    throw "找不到 HTCondor MSI：$MsiSource"
}

if ([System.IO.Path]::GetExtension($MsiSource).ToLowerInvariant() -ne ".msi") {
    throw "指定文件不是 MSI：$MsiSource"
}

$thirdPartyDir = Join-Path $ProjectRoot "third_party\htcondor"
$installerDir = Join-Path $ProjectRoot "installer\htcondor"
$runtimeDir = Join-Path $ProjectRoot "backend\runtime\htcondor"
$logsDir = Join-Path $ProjectRoot "backend\logs\htcondor"

@($thirdPartyDir, $installerDir, $runtimeDir, $logsDir) | ForEach-Object {
    New-Item -ItemType Directory -Force -Path $_ | Out-Null
}

$targetMsi = Join-Path $thirdPartyDir "condor-Windows-x64.msi"
$sourceFull = [System.IO.Path]::GetFullPath($MsiSource)
$targetFull = [System.IO.Path]::GetFullPath($targetMsi)

if (-not [string]::Equals($sourceFull, $targetFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    Copy-Item -LiteralPath $MsiSource -Destination $targetMsi -Force
}
else {
    Write-Host "MSI 已经位于目标目录，跳过复制。" -ForegroundColor Cyan
}

$productName = Get-MsiProperty -Path $targetMsi -Property "ProductName"
$productVersion = Get-MsiProperty -Path $targetMsi -Property "ProductVersion"
$manufacturer = Get-MsiProperty -Path $targetMsi -Property "Manufacturer"
$productCode = Get-MsiProperty -Path $targetMsi -Property "ProductCode"

$hash = Get-FileHash -LiteralPath $targetMsi -Algorithm SHA256
$signature = Get-AuthenticodeSignature -LiteralPath $targetMsi

$manifest = [ordered]@{
    component = "HTCondor"
    product_name = $productName
    product_version = $productVersion
    manufacturer = $manufacturer
    product_code = $productCode
    architecture = "x64"
    installer = "condor-Windows-x64.msi"
    installer_size_bytes = (Get-Item -LiteralPath $targetMsi).Length
    sha256 = $hash.Hash.ToLowerInvariant()
    signature_status = [string]$signature.Status
    signer_subject = if ($signature.SignerCertificate) {
        $signature.SignerCertificate.Subject
    } else {
        ""
    }
    bundled_install_dir = "C:\Condor"
    prepared_at_utc = [DateTime]::UtcNow.ToString("o")
}

$manifestPath = Join-Path $thirdPartyDir "manifest.json"
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$hashPath = Join-Path $thirdPartyDir "condor-Windows-x64.msi.sha256"
"$($hash.Hash.ToLowerInvariant())  condor-Windows-x64.msi" |
    Set-Content -LiteralPath $hashPath -Encoding ASCII

Write-Host ""
Write-Host "HTCondor 准备工作完成。" -ForegroundColor Green
Write-Host "项目根目录：$ProjectRoot"
Write-Host "MSI：$targetMsi"
Write-Host "产品名称：$productName"
Write-Host "版本：$productVersion"
Write-Host "SHA256：$($hash.Hash)"
Write-Host "数字签名状态：$($signature.Status)"
Write-Host "清单：$manifestPath"
Write-Host ""
Write-Host "本脚本没有安装 HTCondor，也没有修改现有 Dask 代码。" -ForegroundColor Yellow

if ([string]$signature.Status -ne "Valid") {
    Write-Warning "MSI 数字签名状态不是 Valid。请先核实下载来源，不要继续自动安装。"
}
