param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$problems = [System.Collections.Generic.List[string]]::new()

$largeFiles = Get-ChildItem -LiteralPath $repoRoot -Recurse -File -Force |
    Where-Object { $_.Length -ge 100MB -and $_.FullName -notmatch '[\\/]\.git[\\/]' }
foreach ($file in $largeFiles) {
    $problems.Add("GitHub-size file: $($file.FullName) ($($file.Length) bytes)")
}

$forbiddenNames = @('.env', 'APItext.ipynb', 'credentials.json')
foreach ($name in $forbiddenNames) {
    Get-ChildItem -LiteralPath $repoRoot -Recurse -File -Force -Filter $name |
        Where-Object { $_.FullName -notmatch '[\\/]\.git[\\/]' } |
        ForEach-Object { $problems.Add("Forbidden credential file: $($_.FullName)") }
}

$credentialPattern = @(
    ('AI' + 'za[0-9A-Za-z_-]{20,}'),
    ('gh' + '[pousr]_[0-9A-Za-z]{20,}'),
    ('-----BEGIN ' + '(RSA |EC |OPENSSH )?PRIVATE KEY-----')
) -join '|'
$scan = & rg -n --hidden `
    -g '!.git/**' -g '!*.pdf' -g '!*.png' -g '!*.jpg' -g '!*.patch' `
    $credentialPattern $repoRoot 2>$null
if ($LASTEXITCODE -eq 0) {
    $scan | ForEach-Object { $problems.Add("Credential-like text: $_") }
} elseif ($LASTEXITCODE -gt 1) {
    throw "rg failed with exit code $LASTEXITCODE"
}

if ($problems.Count -gt 0) {
    $problems | ForEach-Object { Write-Error $_ }
    exit 1
}

Write-Host 'Submission verification passed: no >=100 MB files, forbidden credential files, or credential-like text.'
