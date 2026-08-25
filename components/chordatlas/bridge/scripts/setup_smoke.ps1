param(
    [switch]$Force,
    [switch]$RegenerateMesh,
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
$config = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\config\data_builder_london_smoke.json')).Path
& (Join-Path $PSScriptRoot 'build_chordatlas.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$scene = Join-Path $projectRoot 'projects\_mesh_jobs\data_builder_london_smoke_top_level\final\data_builder_london_smoke_top_level_scene.obj'
$arguments = @('--config', $config, 'build')
if ($RegenerateMesh -or -not (Test-Path -LiteralPath $scene -PathType Leaf)) {
    $arguments += '--run-mesh'
}
if ($Force) { $arguments += '--force' }
& (Join-Path $PSScriptRoot 'myproject.ps1') @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& (Join-Path $PSScriptRoot 'myproject.ps1') --config $config validate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Launch) {
    & (Join-Path $PSScriptRoot 'myproject.ps1') --config $config launch
    exit $LASTEXITCODE
}
