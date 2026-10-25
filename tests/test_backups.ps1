# Run using Windows PowerShell 5.1; no real server or scheduled tasks are used.
$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('tools-backup-test-' + [guid]::NewGuid().ToString('N'))
$global:FixtureSource = Join-Path $root 'server'
$local = Join-Path $root 'local'
$scriptPath = Join-Path $PSScriptRoot '..\scripts\pull-backups.ps1'
New-Item -ItemType Directory -Force -Path $global:FixtureSource | Out-Null

function Assert($condition, [string]$message) {
    if (-not $condition) { throw "Assertion failed: $message" }
}

function global:scp.exe {
    $source = [string]$args[-2]
    $target = [string]$args[-1]
    $name = ($source -split '/')[-1]
    Copy-Item -LiteralPath (Join-Path $global:FixtureSource $name) -Destination $target
    $global:LASTEXITCODE = 0
}

try {
    # Parse both production scripts; installing a task is deliberately not executed.
    foreach ($file in @($scriptPath, (Join-Path $PSScriptRoot '..\scripts\install-backup-task.ps1'))) {
        $tokens = $null
        $parseErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$parseErrors) | Out-Null
        Assert (@($parseErrors).Count -eq 0) "Parse errors in $file"
    }
    $entries = @()
    foreach ($day in @('01', '02')) {
        $name = "tools-202610${day}T030000Z-aaaaaaaaaaaa.sqlite3"
        $path = Join-Path $global:FixtureSource $name
        Set-Content -LiteralPath $path -Value "fixture $day"
        $entries += @{ filename=$name; sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    @{snapshots=$entries} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $global:FixtureSource 'manifest.json')
    & $scriptPath -SshHost fixture -RemoteDirectory /fake -Destination $local
    Assert (@(Get-ChildItem -LiteralPath $local -Filter '*.sqlite3').Count -eq 2) 'Both available snapshots downloaded'
    Assert (Test-Path -LiteralPath (Join-Path $local 'last-success.txt')) 'Success status written'
    & $scriptPath -SshHost fixture -RemoteDirectory /fake -Destination $local
    Assert (@(Get-ChildItem -LiteralPath $local -Filter '*.sqlite3').Count -eq 2) 'Second run is idempotent'

    # A modified download must not be published or trigger rotation of good backups.
    $badName = 'tools-20261003T030000Z-bbbbbbbbbbbb.sqlite3'
    Set-Content -LiteralPath (Join-Path $global:FixtureSource $badName) -Value 'tampered'
    @{snapshots=@(@{filename=$badName; sha256=('0' * 64)})} | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $global:FixtureSource 'manifest.json')
    $caught = $false
    try { & $scriptPath -SshHost fixture -RemoteDirectory /fake -Destination $local -Keep 1 } catch { $caught = $true }
    Assert $caught 'Checksum mismatch fails the download'
    Assert (-not (Test-Path -LiteralPath (Join-Path $local $badName))) 'Bad snapshot not published'
    Assert (@(Get-ChildItem -LiteralPath $local -Filter '*.sqlite3').Count -eq 2) 'Good snapshots kept after failure'
    Assert (Test-Path -LiteralPath (Join-Path $local 'errors.log')) 'Error logged'

    # Reject path traversal in an untrusted manifest before invoking scp for that entry.
    @{snapshots=@(@{filename='../outside.sqlite3'; sha256=('0' * 64)})} | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $global:FixtureSource 'manifest.json')
    $caught = $false
    try { & $scriptPath -SshHost fixture -RemoteDirectory /fake -Destination $local } catch { $caught = $true }
    Assert $caught 'Invalid filename rejected'

    # Rotation affects only our snapshot names and only after a successful sync.
    Set-Content -LiteralPath (Join-Path $local 'personal.sqlite3') -Value 'unrelated'
    @{snapshots=$entries} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $global:FixtureSource 'manifest.json')
    & $scriptPath -SshHost fixture -RemoteDirectory /fake -Destination $local -Keep 1
    Assert (Test-Path -LiteralPath (Join-Path $local 'personal.sqlite3')) 'Unrelated database kept'
    Assert (@(Get-ChildItem -LiteralPath $local -Filter 'tools-*.sqlite3').Count -eq 1) 'Retention enforced'
    Write-Output 'Windows backup checks passed.'
} finally {
    Remove-Item function:global:scp.exe -ErrorAction SilentlyContinue
    Remove-Variable FixtureSource -Scope Global -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
