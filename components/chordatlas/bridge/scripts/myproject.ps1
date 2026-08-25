param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$MyProjectArgs
)

$ErrorActionPreference = 'Stop'
$bridgeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$conda = $env:CONDA_EXE
if ([string]::IsNullOrWhiteSpace($conda)) {
    $condaCommand = Get-Command conda -ErrorAction SilentlyContinue
    if ($null -eq $condaCommand) {
        throw 'Conda was not found. Activate Conda or set CONDA_EXE.'
    }
    $conda = $condaCommand.Source
}

& $conda run --no-capture-output -n sat3dgen `
    python -B (Join-Path $bridgeRoot 'run.py') @MyProjectArgs
exit $LASTEXITCODE
