param(
    [switch]$Force,
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
$config = (Resolve-Path -LiteralPath (
    Join-Path $PSScriptRoot '..\config\data_builder_london_on_demand.json'
)).Path

& (Join-Path $PSScriptRoot 'build_chordatlas.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$arguments = @('--config', $config, 'build')
if ($Force) { $arguments += '--force' }
& (Join-Path $PSScriptRoot 'myproject.ps1') @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& (Join-Path $PSScriptRoot 'myproject.ps1') --config $config validate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Launch) {
    if (-not $env:GOOGLE_MAPS_API_KEY) {
        Write-Warning 'GOOGLE_MAPS_API_KEY is not set. The GUI can open, but an on-demand satellite job will stop before downloading.'
    }
    & (Join-Path $PSScriptRoot 'myproject.ps1') --config $config launch
    exit $LASTEXITCODE
}

Write-Host 'OSM-only on-demand workspace is ready.'
Write-Host "Launch with: $PSScriptRoot\launch_gui.ps1 -Config $config"
