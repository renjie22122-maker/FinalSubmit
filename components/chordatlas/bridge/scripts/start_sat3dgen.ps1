param(
    [ValidateRange(1, 65535)]
    [int]$Port = 7860,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'
$conda = $env:CONDA_EXE
if ([string]::IsNullOrWhiteSpace($conda)) {
    $condaCommand = Get-Command conda -ErrorAction SilentlyContinue
    if ($null -eq $condaCommand) {
        throw 'Conda was not found. Activate Conda or set CONDA_EXE.'
    }
    $conda = $condaCommand.Source
}
$satRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..\sat3dgen\Sat3DGen')).Path
$app = Join-Path $satRoot 'app.py'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$logDir = Join-Path $projectRoot 'logs\sat3dgen'

if (-not (Test-Path -LiteralPath $app -PathType Leaf)) {
    throw "Sat3DGen app not found: $app"
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host "Sat3DGen/Gradio is already listening on port $Port."
    exit 0
}

$previousPort = $env:GRADIO_SERVER_PORT
$env:GRADIO_SERVER_PORT = "$Port"
try {
    $arguments = @(
        'run', '--no-capture-output', '-n', 'sat3dgen',
        'python', '-B', $app
    )
    if ($Foreground) {
        & $conda @arguments
        exit $LASTEXITCODE
    }

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $stdout = Join-Path $logDir 'stdout.log'
    $stderr = Join-Path $logDir 'stderr.log'
    $process = Start-Process -FilePath $conda -ArgumentList $arguments `
        -WorkingDirectory $satRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    Write-Host "Sat3DGen started (PID $($process.Id), port $Port)."
    Write-Host "Logs: $stdout and $stderr"
} finally {
    if ($null -eq $previousPort) {
        Remove-Item Env:GRADIO_SERVER_PORT -ErrorAction SilentlyContinue
    } else {
        $env:GRADIO_SERVER_PORT = $previousPort
    }
}
