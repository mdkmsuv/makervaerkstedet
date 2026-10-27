$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'Setup-School.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$statements = @($ast.EndBlock.Statements | Where-Object {
    ($_.Extent.Text.StartsWith('if ($AcceptInstall -and')) -or
    ($_.Extent.Text.StartsWith('$schoolPorts =')) -or
    ($_.Extent.Text.StartsWith('if (@($schoolPorts'))
})
if ($statements.Count -ne 3) { throw 'De isolerede preflight-kontroller kunne ikke findes.' }
# Match the production script's strict-mode behavior.  Without this, a
# zero-result Where-Object pipeline is silently treated as null and the
# unattended all-arguments case misses the Windows PowerShell regression.
$validate = [scriptblock]::Create($ast.ParamBlock.Extent.Text + "`nSet-StrictMode -Version Latest`n" + (($statements | ForEach-Object { $_.Extent.Text }) -join "`n"))
$parameters = @{AcceptInstall=$true;SchoolName='Test';Role='vm';RoleIp='192.0.2.10';StudentSubnet='192.0.2.0/24';Theme='green';PrintBackendPort=18765;DesignBackendPort=18766;TeacherBackendPort=18767;TeacherPort=18780}
# Keep an explicit valid invocation so the strict-mode zero-missing-fields
# path is exercised before checking rejected input.
& $validate @parameters
function Assert-Rejected([hashtable]$Values) {
    $rejected = $false
    try { & $validate @Values } catch { $rejected = $true }
    if (-not $rejected) { throw 'Ugyldig skoleopsætning blev accepteret.' }
}
foreach ($required in @('SchoolName','Role','RoleIp','StudentSubnet','Theme')) {
    $invalid = $parameters.Clone(); $invalid.Remove($required); Assert-Rejected $invalid
}
$invalid = $parameters.Clone(); $invalid.TeacherPort = $invalid.PrintBackendPort; Assert-Rejected $invalid
$invalid = $parameters.Clone(); $invalid.TeacherPort = 8774; Assert-Rejected $invalid
$invalid = $parameters.Clone(); $invalid.PrintBackendPort = 65536; Assert-Rejected $invalid
$invalid = $parameters.Clone(); $invalid.TeacherBackendPort = 0; Assert-Rejected $invalid
Write-Output 'Skoleopsætningens unattended- og portkontrakt bestod.'
