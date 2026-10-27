# Called by the machine-wide installer after administrator consent.
$ErrorActionPreference = 'Stop'
function Enable-PrintLan([string]$InstallPath, [int]$Port, [string]$TaskName) {
    if ($Port -lt 1024 -or $Port -gt 65535 -or $TaskName -notmatch '^Printvaerksted[\w-]*$') { throw 'Ugyldig netvaerksopsaetning.' }
    $pythonPath = Join-Path ([IO.Path]::GetFullPath($InstallPath)) 'runtime\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Printvaerksteds programfiler mangler.' }
    $ruleName = $TaskName + '-LAN'
    $existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
    if ($existing -and $existing.Group -ne 'Printvaerksted') { throw 'Firewallnavnet bruges af en anden regel.' }
    # Do not silently override explicit denials or school IT policies.
    $blocked = Get-NetFirewallApplicationFilter -Program $pythonPath -ErrorAction SilentlyContinue |
        Get-NetFirewallRule -ErrorAction SilentlyContinue |
        Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' }
    foreach ($blockRule in $blocked) {
        $blockPorts = $blockRule | Get-NetFirewallPortFilter
        if ("$($blockPorts.Protocol)" -notin @('TCP','6','Any','256')) { continue }
        foreach ($range in $blockPorts.LocalPort) {
            $parts = "$range".Split('-')
            if ($range -eq 'Any' -or "$range" -eq "$Port" -or
                ($parts.Count -eq 2 -and $Port -ge [int]$parts[0] -and $Port -le [int]$parts[1])) {
                throw 'Windows har en eksplicit firewallspaerring for Printvaerksted. Skolens IT skal afklare den, foer LAN-adgang kan aabnes.'
            }
        }
    }
    $options = @{Name=$ruleName; DisplayName='Printvaerksted - skolens lokalnet';
        Description='Elevside og laererside fra samme lokale netvaerk.';
        Enabled='True'; Direction='Inbound'; Action='Allow'; Profile='Any';
        Program=$pythonPath; Protocol='TCP'; LocalPort=$Port;
        RemoteAddress='LocalSubnet'; EdgeTraversalPolicy='Block'}
    if ($existing) {
        $displayName = $options.DisplayName
        $options.Remove('DisplayName')
        $null = Set-NetFirewallRule @options -NewDisplayName $displayName
    }
    else { $null = New-NetFirewallRule @options -Group 'Printvaerksted' }
    $rule = Get-NetFirewallRule -Name $ruleName -ErrorAction Stop
    $ports = $rule | Get-NetFirewallPortFilter
    $addresses = $rule | Get-NetFirewallAddressFilter
    $application = $rule | Get-NetFirewallApplicationFilter
    if ($rule.Enabled -ne 'True' -or $rule.Action -ne 'Allow' -or $rule.Direction -ne 'Inbound' -or
        "$($ports.LocalPort)" -ne "$Port" -or "$($ports.Protocol)" -notin @('TCP','6') -or
        "$($addresses.RemoteAddress)" -ne 'LocalSubnet' -or $application.Program -ne $pythonPath) {
        throw 'Windows-netvaerksreglen kunne ikke bekraeftes.'
    }
}

function Write-PrintNetworkInfo([string]$InstallPath, [int]$Port) {
    $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.AddressState -eq 'Preferred' -and -not $_.SkipAsSource -and
            $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -ExpandProperty IPAddress -Unique)
    $lines = @('PRINTVAERKSTED PAA SKOLENS NETVAERK', '',
        'Aabn denne adresse i en browser paa en anden computer:',
        "http://$($env:COMPUTERNAME):$Port/", '',
        'Hvis computernavnet ikke virker, proev en af disse IP-adresser:')
    foreach ($address in $addresses) { $lines += "http://${address}:$Port/" }
    $lines += @('', 'Laerersiden har /laerer efter adressen og bruger den aftalte laererkode.',
        'Alle bruger samme printere og samme koe. Installer kun koeprogrammet paa vaertscomputeren.',
        'Vaertscomputeren skal vaere taendt og paa netvaerket. Browseren maa gerne vaere lukket.',
        'Adgangen er begraenset til det lokale subnet. Skolens netvaerk kan have yderligere spaerringer.',
        'IP-adresser kan aendre sig. Skolens IT kan reservere en fast adresse til computeren.')
    $lines | Set-Content -LiteralPath (Join-Path $InstallPath 'NETVAERK.txt') -Encoding UTF8
}
