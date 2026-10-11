<#
.SYNOPSIS
  Registers HomeFabric scheduled tasks on Windows (run in an elevated PowerShell).
.DESCRIPTION
  Creates three tasks running as the given account:
    HomeFabric Web            - starts the web server at boot
    HomeFabric Schedules      - process_schedules every minute
    HomeFabric Notifications  - process_notifications every minute
    HomeFabric Backup         - nightly backup at 02:30
  Overlapping runs are prevented by the commands themselves and by
  MultipleInstances=IgnoreNew.
.EXAMPLE
  .\install-tasks.ps1 -InstallDir C:\HomeFabric -ConfigFile C:\HomeFabric\fabric.toml -BackupDir D:\Backups\HomeFabric
#>
param(
  [Parameter(Mandatory)] [string] $InstallDir,
  [Parameter(Mandatory)] [string] $ConfigFile,
  [string] $BackupDir = "$InstallDir\backups",
  [string] $User = "$env:USERDOMAIN\$env:USERNAME"
)
$ErrorActionPreference = "Stop"
$exe = Join-Path $InstallDir ".venv\Scripts\homefabric.exe"
if (-not (Test-Path $exe)) { throw "Not found: $exe. Install HomeFabric into $InstallDir\.venv first." }
[Environment]::SetEnvironmentVariable("FABRIC_CONFIG_FILE", $ConfigFile, "Machine")
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -StartWhenAvailable
$cred = Get-Credential -UserName $User -Message "Account that runs HomeFabric"

function Register-FabricTask($name, $arguments, $trigger, $limit) {
  $action = New-ScheduledTaskAction -Execute $exe -Argument $arguments -WorkingDirectory $InstallDir
  $s = $settings
  if ($limit) { $s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit $limit -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) }
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $s `
    -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
  Write-Host "Registered $name"
}

$everyMinute = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)
Register-FabricTask "HomeFabric Web" "serve" (New-ScheduledTaskTrigger -AtStartup) ([TimeSpan]::Zero)
Register-FabricTask "HomeFabric Schedules" "process_schedules --once" $everyMinute $null
Register-FabricTask "HomeFabric Notifications" "process_notifications --once" $everyMinute $null
Register-FabricTask "HomeFabric Backup" "backup --output `"$BackupDir`"" (New-ScheduledTaskTrigger -Daily -At 2:30am) $null
Start-ScheduledTask -TaskName "HomeFabric Web"
Write-Host "Done. Check the Administration page for scheduler health."
