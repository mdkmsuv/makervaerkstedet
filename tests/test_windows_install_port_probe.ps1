param([string]$InstallScriptPath = (Join-Path $PSScriptRoot '..\system\windows-elevhub\server\install.ps1'))
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskTokens=$null; $taskErrors=$null
$taskAst=[Management.Automation.Language.Parser]::ParseFile((Resolve-Path -LiteralPath $InstallScriptPath).Path,[ref]$taskTokens,[ref]$taskErrors)
if($taskErrors.Count){throw 'Install script does not parse.'}
$taskFunction=@($taskAst.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-PortsFree'},$true))
if($taskFunction.Count -ne 1){throw 'Expected one actual Assert-PortsFree implementation.'}
# An availability check must never start even a transient inbound listener:
# that asks Windows for broad network access for the PowerShell executable.
$taskListeners=@($taskFunction[0].FindAll({param($node)
    ($node -is [Management.Automation.Language.TypeExpressionAst] -and $node.TypeName.FullName -match '(^|\.)TcpListener$') -or
    ($node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and $node.Member.Value -in @('Listen','Start'))
},$true))
if($taskListeners.Count){throw 'Preflight must only bind ports; an inbound listener is forbidden.'}
. ([scriptblock]::Create($taskFunction[0].Extent.Text))

$taskSocket=[Net.Sockets.Socket]::new([Net.Sockets.AddressFamily]::InterNetwork,[Net.Sockets.SocketType]::Stream,[Net.Sockets.ProtocolType]::Tcp)
try {
    $taskSocket.ExclusiveAddressUse=$true
    $taskSocket.Bind([Net.IPEndPoint]::new([Net.IPAddress]::Loopback,0))
    $taskPort=$taskSocket.LocalEndPoint.Port
    $PortBindings=@([pscustomobject]@{Address='127.0.0.1';Port=$taskPort})
    $taskRejected=$false
    try {Assert-PortsFree} catch {if($_.Exception.Message -notlike "*$taskPort*"){throw};$taskRejected=$true}
    if(-not $taskRejected){throw 'An exclusively reserved port must be rejected.'}
} finally {$taskSocket.Dispose()}
# Released ports remain reusable; the preflight also releases its own socket.
Assert-PortsFree
Assert-PortsFree
$taskSocket=[Net.Sockets.Socket]::new([Net.Sockets.AddressFamily]::InterNetwork,[Net.Sockets.SocketType]::Stream,[Net.Sockets.ProtocolType]::Tcp)
try {
    $taskSocket.ExclusiveAddressUse=$true
    $taskSocket.Bind([Net.IPEndPoint]::new([Net.IPAddress]::Loopback,$taskPort))
} finally {$taskSocket.Dispose()}
$PortBindings=@([pscustomobject]@{Address='not-an-ip';Port=$taskPort})
$taskRejected=$false
try {Assert-PortsFree} catch {$taskRejected=$true}
if(-not $taskRejected){throw 'Invalid bind addresses must be rejected.'}
[ordered]@{passed=$true;checks=@('no listener API in actual preflight','reserved port rejected','released port accepted','preflight leaves no socket reserved','invalid address rejected');powershell=$PSVersionTable.PSVersion.ToString()} | ConvertTo-Json -Compress
