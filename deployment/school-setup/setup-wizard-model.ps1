# Pure model and read-only discovery for the Danish Windows setup guide.
# This file deliberately has no param block and performs no installation work.
Set-StrictMode -Version Latest

function ConvertTo-SetupIPv4Number([System.Net.IPAddress]$Address) {
    $b = $Address.GetAddressBytes()
    [uint64]$b[0] * 16777216 + [uint64]$b[1] * 65536 + [uint64]$b[2] * 256 + $b[3]
}

function ConvertFrom-SetupIPv4Number([uint64]$Number) {
    [System.Net.IPAddress]::Parse(('{0}.{1}.{2}.{3}' -f (($Number -shr 24) -band 255), (($Number -shr 16) -band 255), (($Number -shr 8) -band 255), ($Number -band 255)))
}

function ConvertTo-SetupCanonicalSubnet([string]$Address, [int]$PrefixLength) {
    if ($Address -notmatch '^\d{1,3}(?:\.\d{1,3}){3}$') { return $null }
    $ip = $null
    if (-not [System.Net.IPAddress]::TryParse($Address, [ref]$ip) -or
        $ip.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
        $PrefixLength -lt 0 -or $PrefixLength -gt 32 -or $ip.ToString() -cne $Address) { return $null }
    [uint64]$mask = if ($PrefixLength -eq 0) { 0 } else { ([uint64]::MaxValue -shr (64 - $PrefixLength)) -shl (32 - $PrefixLength) }
    $network = (ConvertTo-SetupIPv4Number $ip) -band $mask
    '{0}/{1}' -f (ConvertFrom-SetupIPv4Number $network), $PrefixLength
}

function Test-SetupUnicastIPv4([string]$Address) {
    if ($Address -notmatch '^\d{1,3}(?:\.\d{1,3}){3}$') { return $false }
    $ip = $null
    if (-not [System.Net.IPAddress]::TryParse($Address, [ref]$ip) -or $ip.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or $ip.ToString() -cne $Address) { return $false }
    $n = ConvertTo-SetupIPv4Number $ip
    $first = $ip.GetAddressBytes()[0]
    $second = $ip.GetAddressBytes()[1]
    return $n -ne 0 -and $first -ne 0 -and $first -ne 127 -and -not ($first -eq 169 -and $second -eq 254) -and $first -lt 224
}

function Get-SetupNetworkCandidates {
    $result = @()
    $interfaces = @{}
    try {
        foreach ($item in @(Get-NetIPInterface -AddressFamily IPv4 -ErrorAction Stop)) { $interfaces[[string]$item.InterfaceIndex] = $item }
    } catch { }
    try {
        $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop)
        foreach ($address in $addresses) {
            if (-not (Test-SetupUnicastIPv4 ([string]$address.IPAddress))) { continue }
            if ([string]$address.AddressState -cne 'Preferred') { continue }
            $prefix = [int]$address.PrefixLength
            $subnet = ConvertTo-SetupCanonicalSubnet ([string]$address.IPAddress) $prefix
            if (-not $subnet) { continue }
            $alias = [string]$address.InterfaceAlias
            $iface = $interfaces[[string]$address.InterfaceIndex]
            if ($iface) {
                $connectionState = if ($iface.PSObject.Properties['ConnectionState']) { [string]$iface.ConnectionState } else { '' }
                $operStatus = if ($iface.PSObject.Properties['OperStatus']) { [string]$iface.OperStatus } else { '' }
                if ($connectionState -match 'Disconnected|NotPresent' -or $operStatus -match 'Down') { continue }
            }
            $dhcp = $null
            if ($iface) { $dhcp = ([string]$iface.Dhcp -eq 'Enabled') }
            $gateway = $null
            try {
                $cfg = @(Get-NetIPConfiguration -InterfaceIndex $address.InterfaceIndex -ErrorAction Stop | Select-Object -First 1)
                if ($cfg -and $cfg.IPv4DefaultGateway) { $gateway = [string]$cfg.IPv4DefaultGateway.NextHop }
            } catch { }
            $result += [pscustomobject][ordered]@{ alias=$alias; address=[string]$address.IPAddress; prefixLength=$prefix; subnet=$subnet; dhcp=$dhcp; gateway=$gateway }
        }
    } catch { }
    @($result)
}

function Test-SetupWizardInput([hashtable]$Values) {
    $errors = @(); $parameters = [ordered]@{}; $entry = $null; $connectionSummary = $null
    $deployment = [string]$Values['deployment']; $component = [string]$Values['component']
    if ($deployment -notin @('single-computer','separate-computers')) { $errors += [ordered]@{field='deployment';message='Vælg én computer eller separate computere.'} }
    if ($component -notin @('server','laser-station')) { $errors += [ordered]@{field='component';message='Vælg server eller laserstation.'} }
    if ($component -eq 'laser-station') {
        if ($deployment -ne 'separate-computers') { $errors += [ordered]@{field='deployment';message='En laserstation kræver opsætningen separate computere.'} }
        $path = [string]$Values['connectionFile']
        if (-not $path) { $errors += [ordered]@{field='connectionFile';message='Tryk Vælg fil…, og vælg laser-station-connection.json fra serveren. Tryk derefter Næste.'} }
        elseif (-not (Test-Path -LiteralPath $path -PathType Leaf)) { $errors += [ordered]@{field='connectionFile';message='Filen blev ikke fundet. Vælg serverens laser-station-connection.json-eksport igen.'} }
        elseif ((Get-Item -LiteralPath $path).Length -gt 32768) { $errors += [ordered]@{field='connectionFile';message='Filen er større end 32 KB. Hent en ny laser-station-connection.json fra serveren, og vælg den her. Filen skal ikke håndrettes.'} }
        else {
            try { $raw = [IO.File]::ReadAllText($path, [Text.UTF8Encoding]::new($false, $true)); $json = $raw | ConvertFrom-Json -ErrorAction Stop }
            catch { $errors += [ordered]@{field='connectionFile';message='Filen kunne ikke læses som en forbindelsesfil. Hent en ny laser-station-connection.json fra serveren, og vælg den her. Filen skal ikke håndrettes.'} }
            if ($errors.Count -eq 0) { try {
                if ($null -eq $json -or $json -is [array] -or -not $raw.TrimStart().StartsWith('{')) { throw 'shape' }
                $names = @($json.PSObject.Properties.Name)
                if ('kind' -in $names -and [string]$json.kind -eq 'elevhub-school-connection') { throw 'linux' }
                if ($names.Count -ne 7 -or @('schemaVersion','kind','school_name','server_url','caCertificatePem','station_id','machine' | Where-Object { $_ -notin $names }).Count) { throw 'incomplete' }
                if ($null -eq $json -or $json.schemaVersion -isnot [int] -or [int]$json.schemaVersion -ne 1) { throw 'schema' }
                if ([string]$json.kind -cne 'makervaerkstedet-laserstation-connection') { throw 'kind' }
                if ([string]::IsNullOrWhiteSpace([string]$json.school_name)) { throw 'school' }
                $uri = $null
                if ([string]$json.server_url -notmatch '^https://[^/:]+:\d{1,5}$' -or -not [Uri]::TryCreate([string]$json.server_url, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -cne 'https' -or $uri.Port -lt 1 -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/') { throw 'https' }
                if ([string]$json.station_id -notmatch '^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$') { throw 'station' }
                if ([string]$json.machine -notin @('fbb2','fhexa1')) { throw 'model' }
                if ([string]::IsNullOrWhiteSpace([string]$json.caCertificatePem) -or [string]$json.caCertificatePem -match 'PRIVATE KEY' -or [string]$json.caCertificatePem -notmatch 'BEGIN CERTIFICATE') { throw 'ca' }
                $connectionSummary = [ordered]@{schoolName=[string]$json.school_name;serverUrl=[string]$json.server_url;stationId=[string]$json.station_id;machine=[string]$json.machine}
            } catch {
                $message = switch ([string]$_.Exception.Message) {
                    'linux' { 'Dette er Linux school-connection.json. Vælg serverens laser-station-connection.json-eksport i stedet.'; break }
                    'incomplete' { 'Forbindelsesfilen mangler serverens felter. Vælg en ny laser-station-connection.json-eksport, ikke en håndrettet fil.'; break }
                    'shape' { 'Filen skal være ét JSON-objekt. Vælg en ny laser-station-connection.json-eksport; håndret ikke filen.'; break }
                    default { 'Forbindelsesfilen er ugyldig eller passer ikke til denne laserstation. Vælg en ny laser-station-connection.json-eksport fra serveren; håndret ikke filen.' }
                }
                $errors += [ordered]@{field='connectionFile';message=$message}
            } }
        }
        if ($errors.Count -eq 0) { $entry='Setup-LaserStation.ps1'; $parameters['Action']='Install'; $parameters['ConnectionFile']=$path; $parameters['PackageRoot']=[string]$Values['packageRoot']; $parameters['NoStart']=$true }
    } elseif ($component -eq 'server') {
        $school = [string]$Values['schoolName']
        if ([string]::IsNullOrWhiteSpace($school) -or $school.Length -gt 80 -or @($school.ToCharArray() | Where-Object { [int][char]$_ -lt 32 -or [int][char]$_ -eq 127 }).Count) { $errors += [ordered]@{field='schoolName';message='Skriv skolens navn på 1-80 tegn uden kontroltegn.'} }
        $roleIp = [string]$Values['roleIp']
        if (-not $roleIp) { $errors += [ordered]@{field='roleIp';message='Vælg computerens IPv4-adresse fra netkortlisten, eller få den fra skolens IT.'} }
        elseif (-not (Test-SetupUnicastIPv4 $roleIp)) { $errors += [ordered]@{field='roleIp';message='Adressen kan ikke bruges som serveradresse. Vælg adressen fra netværkskortlisten. Er du i tvivl, så brug Kopiér tjekliste til IT; eksemplet 192.168.10.20 er ikke skolens adresse.'} }
        $subnet = [string]$Values['studentSubnet']; $parts = $subnet -split '/', 2; $canonical = $null; $subnetIp = $null; $prefix = 0
        if ($parts.Count -ne 2 -or -not [int]::TryParse($parts[1], [ref]$prefix) -or $prefix -lt 8 -or $prefix -gt 32 -or -not ($canonical = ConvertTo-SetupCanonicalSubnet $parts[0] $prefix) -or $canonical -cne $subnet) { $errors += [ordered]@{field='studentSubnet';message='Netværket skal skrives som fx 192.168.10.0/24. Brug forslaget eller få værdien fra skolens IT.'} }
        elseif (-not (Test-SetupUnicastIPv4 $parts[0])) { $errors += [ordered]@{field='studentSubnet';message='Området kan ikke bruges som værkstedsnet. Vælg netværkskortets forslag, og få det bekræftet med Kopiér tjekliste til IT.'} }
        if ((Test-SetupUnicastIPv4 $roleIp) -and $canonical) { $r = ConvertTo-SetupIPv4Number ([Net.IPAddress]::Parse($roleIp)); $n = ConvertTo-SetupIPv4Number ([Net.IPAddress]::Parse($parts[0])); $mask = if ($prefix -eq 0) { 0 } else { ([uint64]::MaxValue -shr (64-$prefix)) -shl (32-$prefix) }; if (($r -band $mask) -ne $n) { $errors += [ordered]@{field='roleIp';message='Serveradressen ligger uden for det indtastede værkstedsnet. Vælg netværkskortet igen, eller få begge felter bekræftet med Kopiér tjekliste til IT.'} } }
        if (-not ($Values['addressConfirmed'] -is [bool] -and [bool]$Values['addressConfirmed'])) { $errors += [ordered]@{field='addressConfirmed';message='Bekræft, at serveradressen er stabil eller reserveret af IT.'} }
        if ($Values['theme'] -notin @('red','yellow','green')) { $errors += [ordered]@{field='theme';message='Vælg rød, gul eller grøn.'} }
        if ($Values['machine'] -notin @('fbb2','fhexa1')) { $errors += [ordered]@{field='machine';message='Vælg en understøttet maskinprofil.'} }
        if ($errors.Count -eq 0) { $entry='Setup-School.ps1'; $parameters['PackageRoot']=[string]$Values['packageRoot']; $parameters['SchoolName']=$school; $parameters['Role']=if($deployment -eq 'separate-computers'){'vm'}else{'makerspace'}; $parameters['RoleIp']=$roleIp; $parameters['StudentSubnet']=$subnet; $parameters['Theme']=[string]$Values['theme']; $parameters['Machine']=[string]$Values['machine']; $parameters['AcceptInstall']=$true; if($deployment -eq 'separate-computers'){$parameters['RemoteLaserStation']=$true} }
    }
    # Keep the reviewed choices beside the execution plan.  The WPF worker can
    # send these back through this function after the user reviews the summary;
    # it never needs to trust a parameter splat produced by the UI.
    $reviewValues = [ordered]@{}
    foreach ($key in @('deployment','component','schoolName','roleIp','studentSubnet','theme','machine','addressConfirmed','connectionFile','packageRoot')) {
        if ($Values.ContainsKey($key)) { $reviewValues[$key] = $Values[$key] }
    }
    [ordered]@{valid=($errors.Count -eq 0);errors=@($errors);parameters=$parameters;entry=$entry;values=$reviewValues;connectionSummary=$connectionSummary}
}

function Get-SetupItChecklist([hashtable]$Values) {
    $name = [string]$Values['schoolName']; $computer = [string]$Values['computerName']; if (-not $computer) { $computer = [string]$env:COMPUTERNAME }; $ip = [string]$Values['roleIp']; $cidr = [string]$Values['studentSubnet']
    "Skole: $name`nFundet computer: $computer`nServeradresse: $ip`nVærkstedsnet: server, eventuel lasercomputer og Linux-elevcomputere skal være på samme LAN; en VM følger serverens LAN.`nElevnet: $cidr`nSpørg IT: Er serveradressen stabil eller reserveret, og hvilket LAN/CIDR skal værkstedsnettet bruge?`nIngen nøgler eller hemmeligheder skal indtastes i guiden."
}
