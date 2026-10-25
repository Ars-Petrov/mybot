param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_.@-]*$')]
    [string]$SshHost,
    [ValidatePattern('^/[a-zA-Z0-9/_-]+$')]
    [string]$RemoteDirectory = '/srv/mybot/backups',
    [string]$Destination = "$env:USERPROFILE\ToolsBotBackups",
    [ValidateRange(1,3650)]
    [int]$Keep = 90
)

$ErrorActionPreference = 'Stop'
Get-Command scp.exe -ErrorAction Stop | Out-Null
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$Destination = (Resolve-Path -LiteralPath $Destination).Path
$staging = Join-Path $Destination ('.download-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $staging | Out-Null
$sshOptions = @('-q', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15')
$pattern = '^tools-\d{8}T\d{6}Z-[a-f0-9]{12}\.sqlite3$'

try {
    $manifestPath = Join-Path $staging 'manifest.json'
    & scp.exe @sshOptions "${SshHost}:${RemoteDirectory}/manifest.json" $manifestPath
    if ($LASTEXITCODE -ne 0) { throw 'Could not download manifest. Existing local backups were kept.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if (-not $manifest.snapshots -or @($manifest.snapshots).Count -gt 3650) { throw 'Invalid backup manifest.' }
    $downloaded = 0
    foreach ($entry in $manifest.snapshots) {
        $name = [string]$entry.filename
        $expected = [string]$entry.sha256
        if ($name -cnotmatch $pattern -or $expected -cnotmatch '^[a-f0-9]{64}$') { throw 'Invalid manifest entry.' }
        $target = Join-Path $Destination $name
        if (Test-Path -LiteralPath $target) {
            if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -eq $expected) { continue }
            throw "Existing backup checksum failed: $name. No files were overwritten."
        }
        $partial = Join-Path $staging $name
        & scp.exe @sshOptions "${SshHost}:${RemoteDirectory}/${name}" $partial
        if ($LASTEXITCODE -ne 0) { throw "Download failed: $name. Existing local backups were kept." }
        if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
            throw "Downloaded backup checksum failed: $name"
        }
        Move-Item -LiteralPath $partial -Destination $target
        $downloaded++
    }
    # Retention runs only after every download passed its SHA-256 check.
    Get-ChildItem -LiteralPath $Destination -File |
        Where-Object { $_.Name -cmatch $pattern } |
        Sort-Object Name -Descending | Select-Object -Skip $Keep |
        Remove-Item -Force
    $status = "$(Get-Date -Format o) OK; downloaded=$downloaded; server_snapshots=$(@($manifest.snapshots).Count)"
    Set-Content -LiteralPath (Join-Path $Destination 'last-success.txt') -Value $status
    Write-Output $status
} catch {
    Add-Content -LiteralPath (Join-Path $Destination 'errors.log') -Value "$(Get-Date -Format o) $($_.Exception.Message)"
    throw
} finally {
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
}
