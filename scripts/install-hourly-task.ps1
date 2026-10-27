# Use after successful test-account verification, not while DRY_RUN is enabled.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Install .venv first' }
if (-not (Test-Path (Join-Path $Root '.env'))) { throw 'Create .env first' }

$Action = New-ScheduledTaskAction -Execute $Python -Argument ('"' + (Join-Path $Root 'verifynudge.py') + '"') -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName 'VerifyNudge' -Action $Action -Trigger $Trigger -Settings $Settings -Description 'Hourly Discord verification reminder' -Force | Out-Null
Write-Output 'Created hourly Windows task VerifyNudge'
