param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_.@-]*$')]
    [string]$SshHost,
    [ValidatePattern('^/[a-zA-Z0-9/_-]+$')]
    [string]$RemoteDirectory = '/srv/mybot/backups',
    [string]$Destination = "$env:USERPROFILE\ToolsBotBackups"
)

$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'pull-backups.ps1'
if ($Destination.Contains('"') -or $scriptPath.Contains('"')) { throw 'Double quotes are not allowed in paths.' }
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -SshHost "{1}" -RemoteDirectory "{2}" -Destination "{3}"' -f $scriptPath, $SshHost, $RemoteDirectory, $Destination
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arguments
$triggers = @((New-ScheduledTaskTrigger -Daily -At '20:00'), (New-ScheduledTaskTrigger -AtLogOn))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'ToolsBotBackups' -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description 'Download and verify tools bot backups daily and at sign-in.' -Force | Out-Null
Write-Output 'Task ToolsBotBackups installed. First test: Start-ScheduledTask -TaskName ToolsBotBackups'
