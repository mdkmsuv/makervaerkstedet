function Assert-PlainPath([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item=Get-Item -LiteralPath $Path -Force
    while ($null -ne $item) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Fjernelse afviser reparse point: $($item.FullName)" }
        $item=if ($item -is [IO.DirectoryInfo]) { $item.Parent } else { $item.Directory }
    }
}
function Get-OwnedShortcut([string]$Path, [string]$Target, [string]$WorkingDirectory, [string]$Arguments = '', [string[]]$CompatibleArguments = @()) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    Assert-PlainPath $Path
    $shell=New-Object -ComObject WScript.Shell
    $shortcut=$shell.CreateShortcut($Path)
    $targetMatches=$shortcut.TargetPath -eq $Target
    if ($Target -eq 'powershell.exe') {
        $targetMatches=$targetMatches -or $shortcut.TargetPath -eq (Get-Command powershell.exe -ErrorAction Stop).Source
    }
    # Callers may enumerate exact arguments written by an older installer.
    # This never relaxes target/working-directory ownership or parses arbitrary switches.
    $argumentMatches = $shortcut.Arguments -eq $Arguments -or $CompatibleArguments -contains [string]$shortcut.Arguments
    if (-not $targetMatches -or $shortcut.WorkingDirectory -ne $WorkingDirectory -or -not $argumentMatches) {
        throw "Genvejen er ændret eksternt; intet fjernes: $Path"
    }
    return $true
}

function Test-FirewallAddressEquivalent([string]$Actual, [string]$Expected) {
    # Windows Firewall may expose an IPv4 host rule written as either
    # 192.0.2.10 or 192.0.2.10/32. Accept only that representation change;
    # do not normalize arbitrary firewall expressions into a wider scope.
    $parse = {
        param([string]$Value)
        if ([string]::IsNullOrEmpty($Value) -or $Value -cne $Value.Trim()) { return $null }
        $parts = $Value.Split('/')
        if ($parts.Count -gt 2) { return $null }
        $address = $null
        if (-not [Net.IPAddress]::TryParse($parts[0], [ref]$address) -or
            $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
            $parts[0] -cne $address.ToString() -or
            $address.Equals([Net.IPAddress]::Any)) { return $null }
        $prefix = 32
        $hasPrefix = $false
        if ($parts.Count -eq 2) {
            # Keep exact non-/32 CIDR expressions valid for exact comparison,
            # but do not accept /0, padded masks or a mask outside IPv4.
            if ($parts[1] -notmatch '^(?:[1-9]|[12]\d|3[0-2])$') { return $null }
            $prefix = [int]$parts[1]
            $hasPrefix = $true
        }
        return [pscustomobject]@{ Text=$Value; Address=$address.ToString(); Prefix=$prefix; HasPrefix=$hasPrefix }
    }
    $actualAddress = & $parse $Actual
    $expectedAddress = & $parse $Expected
    if ($null -eq $actualAddress -or $null -eq $expectedAddress) { return $false }
    if ($actualAddress.Text -ceq $expectedAddress.Text) { return $true }
    return ($actualAddress.Prefix -eq 32 -and $expectedAddress.Prefix -eq 32 -and $actualAddress.Address -ceq $expectedAddress.Address)
}

function New-OfflineRemovalLocks([string]$InstallRoot, [string]$DataRoot, [bool]$Generation, [string[]]$AdditionalDataRoots = @()) {
    # FileStream.Lock takes the same exclusive byte-range lock as Python's
    # GenerationLock/InstanceLock on Windows. It fails immediately if a reader
    # or offline maintenance command is still active. Never create a lock here.
    $handles = [Collections.Generic.List[IO.FileStream]]::new()
    try {
        $paths=@()
        if ($Generation) {
            $paths += Join-Path $InstallRoot 'control\generation.lock'
            $operatorLock=Join-Path $InstallRoot 'control\instance.lock'
            if (Test-Path -LiteralPath $operatorLock -PathType Leaf) { $paths += $operatorLock }
        }
        $paths += Join-Path $DataRoot 'instance.lock'
        foreach ($additional in $AdditionalDataRoots) { if ($additional -ine $DataRoot) { $paths += Join-Path $additional 'instance.lock' } }
        foreach ($path in $paths) {
            Assert-PlainPath $path
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Fjernelse kræver den eksisterende driftslås.' }
            # Delete sharing lets this holder remove its owned program tree,
            # while the byte lock excludes any new generation reader/writer.
            $share=[IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
            $handle=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,$share)
            try { $handle.Lock(0,1) } catch { $handle.Dispose(); throw 'En tjeneste eller vedligeholdelse bruger stadig installationen; intet slettes.' }
            $handles.Add($handle)
        }
        return ,$handles
    } catch {
        foreach ($handle in $handles) { $handle.Dispose() }
        throw
    }
}
