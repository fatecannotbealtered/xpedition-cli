# Package the built binary into its release archive and write the archive's
# SHA-256 checksum file.
#
# Shared by .github/workflows/release.yml (a real PyInstaller binary) and
# .github/workflows/artifact-smoke.yml (a synthetic payload), so the packaging
# and the checksum format cannot drift between the release path and the test
# that exists to protect it.
#
# Reads  dist/xpedition-cli[.exe]
# Writes release/<base>.tar.gz or release/<base>.zip
#        release/checksums-<platform>-<arch>.txt

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RunnerOs,
    [Parameter(Mandatory = $true)][string]$RunnerArch
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$version = node -p "require('./package.json').version"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($version)) {
    throw "could not read version from package.json"
}

$platformMap = @{
    "Linux"   = "linux"
    "macOS"   = "darwin"
    "Windows" = "windows"
}
if (-not $platformMap.ContainsKey($RunnerOs)) {
    throw "unsupported runner OS: $RunnerOs"
}
$platform = $platformMap[$RunnerOs]
$arch = if ($RunnerArch -eq "ARM64") { "arm64" } else { "amd64" }
$base = "xpedition-cli-$version-$platform-$arch"

New-Item -ItemType Directory -Force -Path release | Out-Null

if ($platform -eq "windows") {
    Copy-Item dist/xpedition-cli.exe release/xpedition-cli.exe
    Compress-Archive -Path release/xpedition-cli.exe -DestinationPath "release/$base.zip" -Force
    $archive = "release/$base.zip"
} else {
    Copy-Item dist/xpedition-cli release/xpedition-cli
    tar -C release -czf "release/$base.tar.gz" xpedition-cli
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed with exit code $LASTEXITCODE"
    }
    $archive = "release/$base.tar.gz"
}

$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
$name = Split-Path $archive -Leaf

# Write the line with an explicit LF and no BOM. Out-File would use the
# platform newline, which puts CRLF in the Windows checksum file; the merged
# checksums.txt is then unusable with `sha256sum -c` (the trailing CR is read
# as part of the filename) and would break the in-process SHA-256 check that
# SEC-SPEC §5 requires of self-update. Format is GNU's "<hash>  <name>".
[System.IO.File]::WriteAllText(
    (Join-Path (Get-Location) "release/checksums-$platform-$arch.txt"),
    "$hash  $name`n"
)
