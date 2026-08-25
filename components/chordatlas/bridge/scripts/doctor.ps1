param(
    [string]$Config = (Join-Path $PSScriptRoot '..\config\data_builder_london_smoke.json')
)

$runner = Join-Path $PSScriptRoot 'myproject.ps1'
& $runner --config $Config doctor
exit $LASTEXITCODE

