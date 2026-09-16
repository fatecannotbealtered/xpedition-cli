<#
.SYNOPSIS
  Run the deterministic R1/C1/3V3 ChangeSet smoke loop without Xpedition.

.DESCRIPTION
  This validates the CLI contract, confirmation gate, persistence, and
  save/re-read equivalence using MockBackend. It does not claim vendor
  Xpedition runtime compatibility; that still requires a licensed native run.
#>
[CmdletBinding()]
param(
  [string]$WorkDirectory = ''
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command xpedition-cli -ErrorAction SilentlyContinue)) {
  throw 'xpedition-cli was not found on PATH. Install the project first.'
}

if ([string]::IsNullOrWhiteSpace($WorkDirectory)) {
  $WorkDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("xpedition-cli-smoke-" + [guid]::NewGuid().ToString('N'))
}
New-Item -ItemType Directory -Path $WorkDirectory -Force | Out-Null
$project = Join-Path $WorkDirectory 'rc-smoke.json'
$changeset = Join-Path $WorkDirectory 'rc-smoke.changeset.json'
$config = Join-Path $WorkDirectory 'config'

function Invoke-CliJson {
  param([Parameter(Mandatory)][string[]]$Arguments)
  $output = & xpedition-cli @Arguments --compact
  if ($LASTEXITCODE -ne 0) {
    throw "xpedition-cli failed ($LASTEXITCODE): $($output -join ' ')"
  }
  $json = ($output -join "`n") | ConvertFrom-Json
  if (-not $json.ok) {
    throw "xpedition-cli returned an error: $($json.error.code) $($json.error.message)"
  }
  return $json
}

function Get-Signature {
  param([Parameter(Mandatory)]$Data)
  [ordered]@{
    components = @($Data.components | Sort-Object refdes | ForEach-Object {
      [ordered]@{ refdes = $_.refdes; part_number = $_.part_number; x = $_.x; y = $_.y }
    })
    nets = @($Data.nets | Sort-Object name | ForEach-Object {
      [ordered]@{ name = $_.name }
    })
    connections = @($Data.connections | Sort-Object net | ForEach-Object {
      [ordered]@{ net = $_.net; pins = @($_.pins | Sort-Object) }
    })
  } | ConvertTo-Json -Depth 20 -Compress
}

$changesetBody = @{
  project = 'rc-smoke'
  base_revision = 'R00'
  operations = @(
    @{ type = 'place_component'; refdes = 'R1'; part_number = 'RES-10K'; x = 100; y = 80 }
    @{ type = 'place_component'; refdes = 'C1'; part_number = 'CAP-100N'; x = 120; y = 80 }
    @{ type = 'create_net'; name = '3V3' }
    @{ type = 'connect'; net = '3V3'; pins = @('R1.1', 'C1.1') }
  )
}
$changesetJson = $changesetBody | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($changeset, $changesetJson, (New-Object System.Text.UTF8Encoding($false)))

$preview = Invoke-CliJson @('project', 'init', '--project', $project, '--name', 'rc-smoke', '--dry-run')
Invoke-CliJson @('project', 'init', '--project', $project, '--name', 'rc-smoke', '--confirm', $preview.data.confirm_token) | Out-Null
Invoke-CliJson @('change', 'validate', '--changeset', $changeset) | Out-Null

$applyPreview = Invoke-CliJson @('change', 'apply', '--backend', 'mock', '--project', $project, '--changeset', $changeset, '--dry-run')
$applied = Invoke-CliJson @('change', 'apply', '--backend', 'mock', '--project', $project, '--changeset', $changeset, '--confirm', $applyPreview.data.confirm_token, '--backup')
if (-not $applied.data.verification.valid) {
  throw 'ChangeSet verification failed.'
}

$first = Invoke-CliJson @('project', 'snapshot', '--backend', 'mock', '--project', $project)
$firstData = $first.data
if (@($firstData.components | Where-Object refdes -eq 'R1').Count -ne 1) { throw 'R1 was not persisted.' }
if (@($firstData.components | Where-Object refdes -eq 'C1').Count -ne 1) { throw 'C1 was not persisted.' }
if (@($firstData.nets | Where-Object name -eq '3V3').Count -ne 1) { throw '3V3 was not persisted.' }
if (@($firstData.connections | Where-Object { $_.net -eq '3V3' -and (@($_.pins) -contains 'R1.1') -and (@($_.pins) -contains 'C1.1') }).Count -ne 1) { throw 'R1.1/C1.1 connection was not persisted.' }

$second = Invoke-CliJson @('project', 'snapshot', '--backend', 'mock', '--project', $project)
if ((Get-Signature $first.data) -ne (Get-Signature $second.data)) {
  throw 'Save/re-read snapshot mismatch.'
}

[ordered]@{
  ok = $true
  backend = 'mock'
  project = $project
  checks = @('R1 placed', 'C1 placed', '3V3 created', 'R1.1 connected to C1.1', 'save/re-read equal')
  note = 'Native Xpedition runtime compatibility still requires a licensed native smoke run.'
} | ConvertTo-Json -Depth 10
