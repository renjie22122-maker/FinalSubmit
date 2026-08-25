param(
    [string]$Config = (Join-Path $PSScriptRoot '..\config\data_builder_london_smoke.json')
)

& (Join-Path $PSScriptRoot 'myproject.ps1') --config $Config launch
exit $LASTEXITCODE

