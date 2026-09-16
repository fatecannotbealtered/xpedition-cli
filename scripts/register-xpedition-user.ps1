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
            Where-Object { Test-Path (Join-Path $_ "common\win64\register\ExpeditionPCB.reg") } |
            Select-Object -First 1
        if ($candidate) { $SddHome = $candidate }
    }
}

if (-not $SddHome) {
    throw "Set XPEDITION_SDD_HOME to the installed SDD_HOME directory."
}

$SddHome = (Resolve-Path -LiteralPath $SddHome).Path
$escapedSddHome = $SddHome.Replace('\', '\\')

$templates = @(
    @{ Name = "ExpeditionPCB.reg"; Replacements = @{ "SDD_HOME" = $escapedSddHome; "SDD_PLATFORM" = "win64"; "SDD_WG" = "wg" } },
    @{ Name = "viewdraw.reg"; Replacements = @{ "SDD_HOME" = $escapedSddHome; "SDD_PLATFORM" = "win64"; "SDD_WV" = "wv" } }
)
foreach ($item in $templates) {
    $template = Join-Path $SddHome ("common\win64\register\" + $item.Name)
    if (-not (Test-Path -LiteralPath $template)) {
        throw "$($item.Name) was not found under $SddHome"
    }
    $expanded = (Get-Content -LiteralPath $template -Raw)
    foreach ($replacement in $item.Replacements.GetEnumerator()) {
        $expanded = $expanded.Replace($replacement.Key, $replacement.Value)
    }
    $expanded = $expanded.Replace('[HKEY_CLASSES_ROOT\', '[HKEY_CURRENT_USER\Software\Classes\')
    $temporaryReg = Join-Path $env:TEMP ("xpedition-user-" + [Guid]::NewGuid().ToString('N') + ".reg")
    try {
        Set-Content -LiteralPath $temporaryReg -Value $expanded -Encoding ASCII
        $previousErrorAction = $ErrorActionPreference
        try {
            # reg.exe writes its localized success message to the error stream
            # on some Windows builds.  Treat that message as diagnostic text,
            # and use the native exit code as the success signal.
            $ErrorActionPreference = "Continue"
            $registrationOutput = @(& reg.exe import $temporaryReg 2>&1 | ForEach-Object { "$($_)" })
            $registrationExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorAction
        }
        $registrationOutput | ForEach-Object { Write-Output $_ }
        if ($registrationExitCode -ne 0) {
            throw "reg.exe import for $($item.Name) exited with code $registrationExitCode"
        }
    }
    finally {
        Remove-Item -LiteralPath $temporaryReg -Force -ErrorAction SilentlyContinue
    }
}

$registered = (Test-Path 'Registry::HKEY_CLASSES_ROOT\MGCPCB.ExpeditionPCBApplication') -or
    (Test-Path 'Registry::HKEY_CLASSES_ROOT\MGCPCB.Application')
if (-not $registered) {
    throw "the current-user Xpedition COM registration was not visible after import"
}

Write-Output "Xpedition COM registration completed for the current user ($SddHome)"
