param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$mavenCommand = Get-Command mvn.cmd -ErrorAction SilentlyContinue
if ($null -eq $mavenCommand) {
    $mavenCommand = Get-Command mvn -ErrorAction SilentlyContinue
}
if ($null -eq $mavenCommand) {
    throw 'Maven was not found on PATH.'
}
$maven = $mavenCommand.Source

Push-Location $projectRoot
try {
    & $maven -DskipTests package
    if ($LASTEXITCODE -ne 0) {
        throw "Maven package failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$jar = Join-Path $projectRoot 'target\chordatlas-0.0.1.jar'
if (-not (Test-Path -LiteralPath $jar -PathType Leaf)) {
    throw "Build completed without expected JAR: $jar"
}
Write-Host "ChordAtlas JAR ready: $jar"
