param(
    [string]$Config = (Join-Path $PSScriptRoot '..\config\data_builder_london_smoke.json'),
    [switch]$Execute
)

$arguments = @('--config', $Config, 'start-frankengan')
if ($Execute) { $arguments += '--execute' }
& (Join-Path $PSScriptRoot 'myproject.ps1') @arguments
exit $LASTEXITCODE
