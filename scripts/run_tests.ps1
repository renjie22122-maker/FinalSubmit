param(
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$bridge = Join-Path $repoRoot 'components\chordatlas\bridge'
$sat3dgen = Join-Path $repoRoot 'components\sat3dgen'
$researchScripts = Join-Path $repoRoot 'research\scripts'
$oldPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = @(
    (Join-Path $bridge 'src'),
    $sat3dgen,
    $researchScripts
) -join [System.IO.Path]::PathSeparator

function Invoke-TestGroup {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Write-Host "Running $Label"
    Push-Location $WorkingDirectory
    try {
        & $Python -B @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Label failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

try {
    Invoke-TestGroup $bridge @('-m', 'unittest', 'discover', '-s', 'tests', '-v') 'ChordAtlas bridge tests'
    Invoke-TestGroup $researchScripts @('-m', 'unittest', 'discover', '-s', '.', '-p', 'test_*.py', '-v') 'large-image extension tests'
    Write-Host 'All Python test groups passed.'
} finally {
    $env:PYTHONPATH = $oldPythonPath
}
