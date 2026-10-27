$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\lan-access.ps1')
$testPath = Join-Path ([IO.Path]::GetTempPath()) ('Printvaerksted-LanTest-' + [guid]::NewGuid())
$null = New-Item -ItemType Directory -Path (Join-Path $testPath 'runtime')
$null = New-Item -ItemType File -Path (Join-Path $testPath 'runtime\python.exe')
$script:rule = $null
$script:denied = $false
$script:created = 0
$script:updated = 0
function Get-NetFirewallRule {
    param($Name, [Parameter(ValueFromPipeline=$true)]$InputObject)
    process {
        if ($Name) { return $script:rule }
        if ($InputObject -and $script:denied) { return [pscustomobject]@{Enabled='True'; Direction='Inbound'; Action='Block'; Protocol='TCP'; LocalPort='Any'} }
    }
}
function Get-NetFirewallApplicationFilter {
    param($Program, [Parameter(ValueFromPipeline=$true)]$InputObject)
    process {
        if ($Program -and $script:denied) { return [pscustomobject]@{Program=$Program} }
        if ($InputObject) { return [pscustomobject]@{Program=$InputObject.Program} }
    }
}
function Get-NetFirewallPortFilter {
    param([Parameter(ValueFromPipeline=$true)]$InputObject)
    process { return [pscustomobject]@{Protocol=$InputObject.Protocol; LocalPort=$InputObject.LocalPort} }
}
function Get-NetFirewallAddressFilter {
    param([Parameter(ValueFromPipeline=$true)]$InputObject)
    process { return [pscustomobject]@{RemoteAddress=$InputObject.RemoteAddress} }
}
function New-NetFirewallRule {
    param($Name,$DisplayName,$Description,$Enabled,$Direction,$Action,$Profile,$Program,$Protocol,$LocalPort,$RemoteAddress,$EdgeTraversalPolicy,$Group)
    $script:created++
    $script:rule = [pscustomobject]$PSBoundParameters
}
function Set-NetFirewallRule {
    param($Name,$NewDisplayName,$Description,$Enabled,$Direction,$Action,$Profile,$Program,$Protocol,$LocalPort,$RemoteAddress,$EdgeTraversalPolicy)
    $script:updated++
    $params = @{} + $PSBoundParameters
    $params['Group'] = 'Printvaerksted'
    $script:rule = [pscustomobject]$params
}
Enable-PrintLan $testPath 8765 'Printvaerksted-Test'
if ($script:created -ne 1 -or $script:rule.RemoteAddress -ne 'LocalSubnet' -or
    $script:rule.Protocol -ne 'TCP' -or $script:rule.LocalPort -ne 8765 -or
    $script:rule.EdgeTraversalPolicy -ne 'Block' -or $script:rule.Profile -ne 'Any') { throw 'LAN scope failed.' }
Enable-PrintLan $testPath 8767 'Printvaerksted-Test'
if ($script:updated -ne 1 -or $script:created -ne 1 -or $script:rule.LocalPort -ne 8767) { throw 'Idempotent update failed.' }
$script:rule.Group = 'Another app'
$rejected = $false
try { Enable-PrintLan $testPath 8765 'Printvaerksted-Test' } catch { $rejected = $true }
if (-not $rejected -or $script:updated -ne 1) { throw 'Unrelated rule was changed.' }
$script:rule.Group = 'Printvaerksted'
$script:denied = $true
$rejected = $false
try { Enable-PrintLan $testPath 8765 'Printvaerksted-Test' } catch { $rejected = $true }
if (-not $rejected -or $script:updated -ne 1) { throw 'Explicit denial was overridden.' }
Write-PrintNetworkInfo $testPath 8765
$info = Get-Content -LiteralPath (Join-Path $testPath 'NETVAERK.txt') -Raw
if (-not $info.Contains("http://$($env:COMPUTERNAME):8765/") -or -not $info.Contains('/laerer')) { throw 'Network addresses missing.' }
Write-Output 'LAN installer checks passed: scoped access, idempotent update, unrelated rules and denials preserved, network address file.'
# Keep test artifacts for review; never delete a computed temporary tree.
