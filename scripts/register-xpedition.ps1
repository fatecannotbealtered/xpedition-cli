[CmdletBinding()]
param(
    [Parameter()]
    [string]$SddHome = $env:XPEDITION_SDD_HOME
)

$ErrorActionPreference = "Stop"

if (-not $SddHome) {
    $license = $env:MGLS_LICENSE_FILE
    if ($license) {
        $licenseParent = Split-Path -Parent $license
        $candidate = Get-ChildItem -LiteralPath $licenseParent -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "SDD_HOME" } |
            Where-Object { Test-Path (Join-Path $_ "common\win64\bin\registrator.exe") } |
            Select-Object -First 1
        if ($candidate) { $SddHome = $candidate }
    }
}

if (-not $SddHome) {
    throw "Set XPEDITION_SDD_HOME to the installed SDD_HOME directory."
}

$SddHome = (Resolve-Path -LiteralPath $SddHome).Path
$registrator = Join-Path $SddHome "common\win64\_bin\registrator.exe"
$workingDirectory = Split-Path -Parent $registrator
if (-not (Test-Path -LiteralPath $registrator)) {
    $registrator = Join-Path $SddHome "common\win64\bin\registrator.exe"
    $workingDirectory = Split-Path -Parent $registrator
}
if (-not (Test-Path -LiteralPath $registrator)) {
    throw "registrator.exe was not found under $SddHome"
}

$targetRoot = Split-Path -Parent $SddHome
$env:TARGET = $targetRoot
$env:SDD_HOME = $SddHome
$env:SDD_PLATFORM = "win64"
$env:SDD_VERSION = (Split-Path -Leaf (Split-Path -Parent $SddHome))
$env:SDD_WG = "wg"
$env:VCO = "ixw"
$env:MENTOR_ROOT = $targetRoot
$mgcHome = Join-Path $targetRoot "MGC_HOME.ixw"
if (Test-Path -LiteralPath $mgcHome) { $env:MGC_HOME = $mgcHome }

Push-Location $workingDirectory
try {
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $registrationOutput = @(& $registrator "-version=$env:SDD_VERSION" 2>&1 | ForEach-Object { "$($_)" })
        $registrationExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    $registered = (Test-Path 'Registry::HKEY_CLASSES_ROOT\MGCPCB.ExpeditionPCBApplication') -or
        (Test-Path 'Registry::HKEY_CLASSES_ROOT\MGCPCB.Application')
    if ($registrationExitCode -ne 0 -or -not $registered) {
        $registrationOutput | ForEach-Object { Write-Error $_ }
        Write-Warning "registrator did not create the Xpedition automation class; importing the official registry template."
        $template = Join-Path $SddHome "common\win64\register\ExpeditionPCB.reg"
        if (-not (Test-Path -LiteralPath $template)) {
            throw "registrator exited with code $registrationExitCode and the official registry template was not found"
        }
        $escapedSddHome = $SddHome.Replace('\', '\\')
        $expanded = (Get-Content -LiteralPath $template -Raw).Replace('SDD_HOME', $escapedSddHome)
        $expanded = $expanded.Replace('SDD_PLATFORM', 'win64').Replace('SDD_WG', 'wg')
        $temporaryReg = Join-Path $env:TEMP ("xpedition-" + [Guid]::NewGuid().ToString('N') + ".reg")
        try {
            Set-Content -LiteralPath $temporaryReg -Value $expanded -Encoding ASCII
            & reg.exe import $temporaryReg 2>&1 | ForEach-Object { Write-Output $_ }
            if ($LASTEXITCODE -ne 0) {
                throw "reg.exe import exited with code $LASTEXITCODE"
            }
        }
        finally {
            Remove-Item -LiteralPath $temporaryReg -Force -ErrorAction SilentlyContinue
        }
    }
}
finally {
    Pop-Location
}

Write-Output "Xpedition COM registration completed for $SddHome"
