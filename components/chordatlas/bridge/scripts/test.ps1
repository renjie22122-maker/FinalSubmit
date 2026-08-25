param()

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
$oldPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $bridgeRoot 'src'
try {
    & $conda run --no-capture-output -n sat3dgen `
        python -B -m unittest discover -s (Join-Path $bridgeRoot 'tests') -v
    exit $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $oldPythonPath
}
