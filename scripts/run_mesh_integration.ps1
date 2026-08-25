param(
    [Parameter(Mandatory = $true)]
    [string]$DataRoot,
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$sat3dgen = Join-Path $repoRoot 'components\sat3dgen'
$dataRootPath = (Resolve-Path -LiteralPath $DataRoot).Path
$driver = Join-Path $sat3dgen 'test_mesh_pipeline_merge.py'
$oldPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = @($sat3dgen, $oldPythonPath) -join [System.IO.Path]::PathSeparator

try {
    Push-Location $dataRootPath
    try {
        & $Python -B $driver
        if ($LASTEXITCODE -ne 0) {
            throw "Mesh integration run failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
} finally {
    $env:PYTHONPATH = $oldPythonPath
}
