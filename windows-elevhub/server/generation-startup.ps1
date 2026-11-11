# Shared command contract for generation-aware Windows entry points.
# These functions do not install, start or stop anything.
function Get-GenerationInstallationId {
    param([Parameter(Mandatory)]$Owner)
    $id = [Guid]::Empty
    if (-not [Guid]::TryParseExact([string]$Owner.ownerId, 'D', [ref]$id) -and
        -not [Guid]::TryParseExact([string]$Owner.ownerId, 'N', [ref]$id)) {
        throw 'Installationens ejer-id er ugyldigt.'
    }
    return $id.ToString('N')
}

function Get-InstallationStartupMode {
    param([Parameter(Mandatory)]$Owner, [Parameter(Mandatory)][string]$InstallRoot)
    if ($Owner.PSObject.Properties.Name -contains 'startupProtocol') {
        if ([string]$Owner.startupProtocol -cne 'generation-v1') { throw 'Ukendt opstartsprotokol.' }
        $null = Get-GenerationInstallationId -Owner $Owner
        return 'generation-v1'
    }
    if (Test-Path -LiteralPath $InstallRoot -PathType Container) {
        $control = @(Get-ChildItem -LiteralPath $InstallRoot -Force | Where-Object Name -eq 'control')
        if ($control.Count) { throw 'Kontrolmappen kræver en generationsejet installation. Gammel opstart afvises.' }
    }
    return 'legacy'
}

function ConvertTo-GenerationCommandLine {
    param([AllowEmptyCollection()][string[]]$Values = @())
    # Win32 argv quoting, including embedded quotes and trailing backslashes.
    return (($Values | ForEach-Object {
        $escaped = [regex]::Replace([string]$_, '(\\*)"', '$1$1\"')
        $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
        '"' + $escaped + '"'
    }) -join ' ')
}

function Get-GenerationCommand {
    param(
        [Parameter(Mandatory)][string]$InstallRoot,
        [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{32}$')][string]$InstallationId,
        [Parameter(Mandatory)][ValidateSet('server','enrollment','school-admin','print','print-server','design','hub','laser-station','boxes-worker')][string]$Entry,
        [ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedSelection,
        [AllowEmptyCollection()][string[]]$Arguments = @()
    )
    $root = [IO.Path]::GetFullPath($InstallRoot)
    $values = @('-B', '-I', (Join-Path $root 'app\generation_runtime.py'),
                '--runtime-root', $root, '--active', '--expected-installation-id', $InstallationId)
    if ($ExpectedSelection) { $values += @('--expected-selection', $ExpectedSelection) }
    $values += @(
                '--entry', $Entry, '--') + @($Arguments)
    return [pscustomobject]@{
        Execute = Join-Path $root 'runtime\python.exe'
        ArgumentList = $values
        Arguments = ConvertTo-GenerationCommandLine -Values $values
        WorkingDirectory = $root
    }
}

function New-GenerationLease {
    param(
        [Parameter(Mandatory)][string]$InstallRoot,
        [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{32}$')][string]$InstallationId,
        [ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedSelection
    )
    if ($ExpectedSelection) {
        $command = Get-GenerationCommand -InstallRoot $InstallRoot -InstallationId $InstallationId -Entry 'school-admin' -ExpectedSelection $ExpectedSelection -Arguments @('lease')
    } else {
        $command = Get-GenerationCommand -InstallRoot $InstallRoot -InstallationId $InstallationId -Entry 'school-admin' -Arguments @('lease')
    }
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = [string]$command.Execute
    $psi.Arguments = [string]$command.Arguments
    $psi.WorkingDirectory = [string]$command.WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new()
    $started = $false
    $process.StartInfo = $psi
    try {
        if (-not $process.Start()) { throw 'Generationsleasen kunne ikke starte.' }
        $started = $true
        $lineTask = $process.StandardOutput.ReadLineAsync()
        if (-not $lineTask.Wait([TimeSpan]::FromSeconds(60))) { throw 'Generationsleasen svarede ikke inden for 60 sekunder.' }
        $line = $lineTask.Result
        try { $snapshot = $line | ConvertFrom-Json } catch { throw 'Generationsleasen returnerede ugyldig JSON.' }
        if ($null -eq $snapshot -or
            [IO.Path]::GetFullPath([string]$snapshot.runtime_root) -ine [IO.Path]::GetFullPath($InstallRoot) -or
            [string]$snapshot.installation_id -ine $InstallationId -or
            [string]$snapshot.selection_sha256 -notmatch '^[a-f0-9]{64}$') {
            throw 'Generationsleasen returnerede en ugyldig aktiv selection.'
        }
        if ($ExpectedSelection -and [string]$snapshot.selection_sha256 -cne $ExpectedSelection) {
            throw 'Generationsleasen matcher ikke den forventede selection.'
        }
        return [pscustomobject]@{ Process=$process; Snapshot=$snapshot }
    } catch {
        if ($started) {
            try { $process.StandardInput.Close() } catch { }
            if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit(5000) }
        }
        $process.Dispose()
        throw
    }
}

function Close-GenerationLease {
    param([Parameter(Mandatory)]$Lease)
    $process = $Lease.Process
    try {
        if ($null -ne $process -and -not $process.HasExited) {
            $process.StandardInput.Close()
            if (-not $process.WaitForExit(5000)) {
                $process.Kill()
                $process.WaitForExit(5000)
            }
        }
    } finally { if ($null -ne $process) { $process.Dispose() } }
}
