param(
    [Parameter(Mandatory = $true)]
    [string] $Path
)
$ErrorActionPreference = 'Stop'
$resolved = (Resolve-Path -LiteralPath $Path).ProviderPath
$utf8 = New-Object System.Text.UTF8Encoding($false, $true)
$envelope = [IO.File]::ReadAllText($resolved, $utf8) | ConvertFrom-Json
if ($envelope.ok -isnot [bool]) { throw 'The file is not a CLI JSON envelope.' }
$receipt = $null
if ($envelope.ok) {
    $receipt = $envelope.data.receipt
} elseif ($null -ne $envelope.error.details) {
    $receipt = $envelope.error.details.receipt
}
$summary = [ordered]@{ ok = [bool] $envelope.ok }
if (-not $envelope.ok) { $summary.error_code = $envelope.error.code }
if ($null -ne $receipt) {
    $summary.operation_id = $receipt.operation_id
    $summary.outcome = $receipt.outcome
    $summary.failed_stage = $receipt.failed_stage
    $summary.independently_reopened = $receipt.extensions.persistence_independently_reopened
}
$budget = $envelope.meta.operation_budget
if ($null -eq $budget) { $budget = $envelope.data.extensions.operation_budget }
if ($null -eq $budget) { $budget = $envelope.error.details.operation_budget }
if ($null -eq $budget -and $null -ne $receipt) {
    $budget = $receipt.extensions.operation_budget
}
if ($null -ne $budget) {
    $summary.budget = [ordered]@{
        profile_id = $budget.profile_id
        wall_ms = $budget.wall_ms
        cumulative_native_ms = $budget.cumulative_native_ms
        native_call_count = $budget.native_call_count
        failed_stage = $budget.failed_stage
    }
}
if ($null -ne $envelope.data.preflight) {
    $summary.preflight = [ordered]@{
        kind = $envelope.data.preflight.kind
        blocked = $envelope.data.preflight.blocked
        can_dispatch = $envelope.data.preflight.can_dispatch
        native_observation_performed = $envelope.data.preflight.native_observation_performed
        steps = $envelope.data.preflight.steps
    }
}
# Only explicit result dimensions are emitted. No token, authorization, journal,
# complete preview, private path, document body or receipt extension is printed.
$summary | ConvertTo-Json -Depth 16 -Compress
