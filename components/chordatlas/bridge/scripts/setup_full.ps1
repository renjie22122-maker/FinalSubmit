param(
    [switch]$ExecuteMesh,
    [switch]$Force,
    [switch]$Launch,
    [double]$MeshTimeoutSeconds = 0
)

$ErrorActionPreference = 'Stop'
$config = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\config\data_builder_london_full.json')).Path
& (Join-Path $PSScriptRoot 'build_chordatlas.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $ExecuteMesh) {
    Write-Host 'Full AOI mesh generation is not being executed. The exact command is:'
    & (Join-Path $PSScriptRoot 'myproject.ps1') --config $config run-mesh
    Write-Host 'Re-run with -ExecuteMesh after checking network/GPU inputs and expected cost/time.'
    exit 0
}

$arguments = @('--config', $config, 'build', '--run-mesh')
if ($Force) { $arguments += '--force' }
if ($MeshTimeoutSeconds -gt 0) { $arguments += @('--mesh-timeout', "$MeshTimeoutSeconds") }
& (Join-Path $PSScriptRoot 'myproject.ps1') @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& (Join-Path $PSScriptRoot 'myproject.ps1') --config $config validate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($Launch) {
    & (Join-Path $PSScriptRoot 'myproject.ps1') --config $config launch
    exit $LASTEXITCODE
}

