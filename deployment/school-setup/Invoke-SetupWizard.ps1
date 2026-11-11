param([Parameter(Mandatory=$true)][string]$RequestBase64,[Parameter(Mandatory=$true)][string]$RunDirectory)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'setup-wizard-model.ps1')
. (Join-Path $PSScriptRoot 'setup-wizard-postflight.ps1')
. (Join-Path $PSScriptRoot 'setup-wizard-runner.ps1')
Invoke-SetupWizardWorker $RequestBase64 $RunDirectory
