[CmdletBinding()]
param([string]$PackageRoot, [switch]$Preview)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $PackageRoot) { $PackageRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
$PackageRoot=[IO.Path]::GetFullPath($PackageRoot)
if ([Threading.Thread]::CurrentThread.ApartmentState -ne 'STA') { throw 'Start guiden med START.cmd eller Windows PowerShell -STA.' }
. (Join-Path $PSScriptRoot 'setup-wizard-model.ps1')
. (Join-Path $PSScriptRoot 'setup-wizard-runner.ps1')
. (Join-Path $PSScriptRoot 'setup-wizard-view.ps1')
$networks=@(Get-SetupNetworkCandidates)
$start = {param($Plan) Start-SetupWizardRun $Plan}
$poll = {param($Handle) Get-SetupWizardRunStatus $Handle}
if ($Preview) {
    $start = {param($Plan) @{preview=$true}}
    $poll = {param($Handle) @{state='failed';message='Dette er en forhåndsvisning. Ingen installation, netværksændring eller Windows-godkendelse er startet.';log='Luk og åbn forhåndsvisningen igen for at afprøve andre valg.';exitCode=$null}}
}
Show-SetupWizard -PackageRoot $PackageRoot -Networks $networks -OnValidate {param($Values) Test-SetupWizardInput $Values} -OnStart $start -OnPoll $poll -OnChecklist {param($Values) Get-SetupItChecklist $Values}
