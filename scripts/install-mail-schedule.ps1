$ErrorActionPreference = 'Stop'
$jobdeskRoot = Split-Path -Parent $PSScriptRoot
$jobdeskPython = Join-Path $jobdeskRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $jobdeskPython)) {
    throw 'Set up the local Python environment before installing the daily worker.'
}
$jobdeskIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$jobdeskAction = New-ScheduledTaskAction -Execute $jobdeskPython -Argument '-m app.worker --scheduled' -WorkingDirectory $jobdeskRoot
$jobdeskTrigger = New-ScheduledTaskTrigger -Daily -At '09:00'
$jobdeskPrincipal = New-ScheduledTaskPrincipal -UserId $jobdeskIdentity -LogonType Interactive -RunLevel Limited
$jobdeskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 60) -MultipleInstances IgnoreNew
$jobdeskTask = New-ScheduledTask -Action $jobdeskAction -Trigger $jobdeskTrigger -Principal $jobdeskPrincipal -Settings $jobdeskSettings -Description 'Jobdesk: daily Gmail application queue and reply checks for Bharti. Requires this Windows user to be signed in; no password is stored.'
Register-ScheduledTask -TaskName 'Jobdesk Bharti Daily Email' -InputObject $jobdeskTask -Force | Select-Object TaskName,State
