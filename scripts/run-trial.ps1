# Representative-engineering private candidate launcher; no development Python.
# This file never creates a scope or copies an operation journal.
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigFile,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = 'Stop'
$trialExpectedExeSha = 'c0abe058b8e0c0999f7d7e31aee49191bf1ac4bb11da18550a83b7c0c564e3e8'
$trialExpectedExeBytes = 50093255

function Get-TrialAbsolutePath([object]$Value, [string]$Name) {
    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value) -or
        ($Value -notmatch '^[A-Za-z]:[\\/]' -and $Value -notmatch '^\\\\[^\\]+\\[^\\]+(?:\\|$)')) {
        throw "$Name must be an explicit absolute path."
    }
    return [IO.Path]::GetFullPath($Value)
}

try {
    # Use this shell's built-in modules, even when an agent inherited the
    # PowerShell 7 module path before starting Windows PowerShell 5.1.
    Import-Module "$PSHOME/Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1" -Force
    Import-Module "$PSHOME/Modules/Microsoft.PowerShell.Management/Microsoft.PowerShell.Management.psd1" -Force
    if (-not $CliArgs -or $CliArgs.Count -eq 0) {
        throw 'Supply public CLI arguments after -ConfigFile <file>.'
    }
    if ($CliArgs[0] -eq '--') { $CliArgs = @($CliArgs | Select-Object -Skip 1) }
    if (-not $CliArgs -or $CliArgs[0] -like '--internal-*') {
        throw 'Only the public CLI entry is supported.'
    }
    $trialConfigPath = Get-TrialAbsolutePath $ConfigFile 'ConfigFile'
    $trialConfig = Get-Content -LiteralPath $trialConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $trialAllowedFields = @('schema_version', 'exe', 'config_dir', 'scope_file',
        'scope_sha256', 'sdd_home', 'mgc_wd', 'temp_dir', 'trace_file')
    foreach ($trialField in $trialConfig.PSObject.Properties.Name) {
        if ($trialAllowedFields -notcontains $trialField) { throw "Unknown configuration field: $trialField" }
    }
    if ($trialConfig.schema_version -ne 1) { throw 'Unsupported trial configuration schema_version.' }
    $trialPaths = @{}
    foreach ($trialField in @('exe', 'config_dir', 'scope_file', 'sdd_home', 'mgc_wd', 'temp_dir', 'trace_file')) {
        $trialPaths[$trialField] = Get-TrialAbsolutePath $trialConfig.$trialField $trialField
    }
    $trialExe = Get-Item -LiteralPath $trialPaths.exe
    if ($trialExe.Length -ne $trialExpectedExeBytes -or
        (Get-FileHash -LiteralPath $trialExe.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne $trialExpectedExeSha) {
        throw 'Bound candidate executable bytes differ; no CLI was dispatched.'
    }
    # The path is always explicit, even during discovery before a grant exists.
    # A missing scope stays invalid/read-only in the product. The launcher cannot grant it.
    if (Test-Path -LiteralPath $trialPaths.scope_file) {
        if ($trialConfig.scope_sha256 -notmatch '^[a-f0-9]{64}$' -or
            (Get-FileHash -LiteralPath $trialPaths.scope_file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $trialConfig.scope_sha256) {
            throw 'Task scope SHA-256 differs; inspect the grant and update the explicit configuration.'
        }
    } elseif ($null -ne $trialConfig.scope_sha256) {
        throw 'A pinned scope file is missing.'
    }
    foreach ($trialField in @('config_dir', 'sdd_home', 'mgc_wd', 'temp_dir')) {
        if (-not (Test-Path -LiteralPath $trialPaths[$trialField] -PathType Container)) {
            throw "$trialField directory is missing; prepare the declared environment first."
        }
    }
    $trialTraceParent = Split-Path -Parent $trialPaths.trace_file
    if (-not (Test-Path -LiteralPath $trialTraceParent -PathType Container)) {
        throw 'trace_file parent directory is missing.'
    }

    # Never inherit another task's scope, worker override or expected process ID.
    # Siemens licensing remains in its installed environment; no credential is read here.
    $trialSavedEnvironment = @{}
    $trialClearNames = @((Get-ChildItem Env: | Where-Object {
        $_.Name -like 'XPEDITION_*' -or $_.Name -in @('PYTHONPATH', 'PYTHONHOME', 'PYTHONIOENCODING', 'TEMP', 'TMP',
            'SDD_HOME', 'SDD_PLATFORM', 'SDD_VERSION', 'SDD_WG', 'SDD_WV', 'SDD_DX',
            'MGC_HOME', 'SDD_MGC_HOME', 'TARGET', 'MENTOR_ROOT', 'MGC_WD', 'WDIR', 'MGC_TMPDIR', 'VCO')
    }).Name)
    $trialValues = @{
        XPEDITION_CLI_CONFIG_DIR = $trialPaths.config_dir
        XPEDITION_CLI_SCOPE_FILE = $trialPaths.scope_file
        XPEDITION_SDD_HOME = $trialPaths.sdd_home
        XPEDITION_MGC_WD = $trialPaths.mgc_wd
        XPEDITION_REQUIRE_EXISTING = '1'
        XPEDITION_NATIVE_TRACE_FILE = $trialPaths.trace_file
        SDD_HOME = $trialPaths.sdd_home
        MGC_WD = $trialPaths.mgc_wd
        WDIR = $trialPaths.mgc_wd + ';' + (Join-Path $trialPaths.sdd_home 'standard')
        MGC_TMPDIR = $trialPaths.temp_dir
        PYTHONIOENCODING = 'utf-8'
        TEMP = $trialPaths.temp_dir
        TMP = $trialPaths.temp_dir
    }
    foreach ($trialName in @($trialClearNames + @($trialValues.Keys) | Select-Object -Unique)) {
        $trialSavedEnvironment[$trialName] = [Environment]::GetEnvironmentVariable($trialName, 'Process')
        [Environment]::SetEnvironmentVariable($trialName, $null, 'Process')
    }
    try {
        foreach ($trialName in $trialValues.Keys) {
            [Environment]::SetEnvironmentVariable($trialName, $trialValues[$trialName], 'Process')
        }
        # Native argument forwarding, no command-string evaluation or alternate adapter.
        & $trialExe.FullName @CliArgs
        $trialExitCode = $LASTEXITCODE
    } finally {
        foreach ($trialName in $trialSavedEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($trialName, $trialSavedEnvironment[$trialName], 'Process')
        }
    }
    exit $trialExitCode
} catch {
    [Console]::Error.WriteLine('Trial launcher: ' + $_.Exception.Message)
    exit 4
}
