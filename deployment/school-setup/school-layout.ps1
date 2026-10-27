# Pure deployment choices shared by the installer and its isolated tests.
function Get-SchoolTeacherAddress([string]$Role, [string]$RoleIp, [bool]$RemoteLaserStation) {
    if ($Role -notin @('vm','makerspace')) { throw 'Skolens serverrolle er ugyldig.' }
    if ($RemoteLaserStation -and $Role -ne 'vm') { throw 'Separat lasercomputer kræver rollen central server/VM.' }
    if ($RemoteLaserStation) { return $RoleIp }
    return '127.0.0.1'
}

function Assert-SchoolLaserProfile([object]$Profile, [bool]$RemoteLaserStation, [string]$Machine) {
    $names = @($Profile.PSObject.Properties.Name)
    if ($RemoteLaserStation) {
        if ('remote_laser_station' -notin $names -or $Profile.remote_laser_station -ne $true -or
            'laser_machine' -notin $names -or $Profile.laser_machine -cne $Machine -or
            'laser_station_id' -notin $names -or $Profile.laser_station_id -cne ('laser-' + $Machine)) {
            throw 'Genoptagelse og gendannelse skal bevare den oprindelige separate laserstation og lasermodel.'
        }
    } elseif ('remote_laser_station' -in $names -and $Profile.remote_laser_station -eq $true) {
        throw 'Den oprindelige installation bruger en separat lasercomputer. Bevar dette installationsvalg.'
    }
}

function New-LaserStationConnection([string]$SchoolName, [string]$ServerAddress, [int]$TeacherPort, [string]$CaCertificatePem, [string]$Machine) {
    $ip = $null
    if (-not [Net.IPAddress]::TryParse($ServerAddress, [ref]$ip) -or $ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
        [Net.IPAddress]::IsLoopback($ip) -or $ip.GetAddressBytes()[0] -eq 0 -or $ip.GetAddressBytes()[0] -ge 224) { throw 'Laserforbindelsen kræver serverens LAN-adresse.' }
    if ($TeacherPort -lt 1 -or $TeacherPort -gt 65535) { throw 'Laserforbindelsens HTTPS-port er ugyldig.' }
    if ($Machine -notin @('fbb2','fhexa1')) { throw 'Vælg en understøttet lasermodel.' }
    if ([string]::IsNullOrWhiteSpace($SchoolName) -or $SchoolName.Length -gt 80 -or $SchoolName -match '[\x00-\x1f\x7f]') { throw 'Skolens navn er ugyldigt.' }
    if ($CaCertificatePem -notmatch '\A\s*-----BEGIN CERTIFICATE-----[\s\S]+-----END CERTIFICATE-----\s*\z' -or $CaCertificatePem -match 'PRIVATE KEY') { throw 'Forbindelsen må kun indeholde skolens offentlige CA-certifikat.' }
    return [ordered]@{
        schemaVersion=1;kind='makervaerkstedet-laserstation-connection';school_name=$SchoolName
        server_url=('https://{0}:{1}' -f $ip,$TeacherPort);caCertificatePem=$CaCertificatePem
        station_id=('laser-' + $Machine);machine=$Machine
    }
}
