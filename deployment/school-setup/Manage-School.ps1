param([string]$PackageRoot, [string]$DataRoot = "$env:ProgramData\ElevhubServer", [switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
$global:LASTEXITCODE = 0
if (-not $PackageRoot) { $PackageRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
function Quote-WindowsArgument {
  param([AllowNull()][string]$Value)
  if ($null -eq $Value -or $Value.IndexOf([char]0) -ge 0 -or $Value.Contains('"')) {
    throw 'En Windows-argument indeholder et ugyldigt citationstegn eller NUL.'
  }
  # Start-Process consumes one command-line string. Double trailing
  # backslashes so the closing quote cannot be swallowed by CommandLineToArgvW.
  $escaped = [regex]::Replace($Value, '(\\+)$', '$1$1')
  return '"' + $escaped + '"'
}
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $ValidateOnly -and -not $isAdmin) {
  $elevationArguments = @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',
    (Quote-WindowsArgument $PSCommandPath), '-PackageRoot', (Quote-WindowsArgument $PackageRoot),
    '-DataRoot', (Quote-WindowsArgument $DataRoot))
  $elevated = Start-Process powershell.exe -Verb RunAs -ArgumentList $elevationArguments -Wait -PassThru
  exit $elevated.ExitCode
}
function Get-InstalledBinding {
  param([string]$RequestedDataRoot)
  $data = [IO.Path]::GetFullPath($RequestedDataRoot)
  $markerPath = Join-Path $data 'installation-owner.json'
  if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { throw 'Den valgte datamappe har ingen aktiv installationsejer.' }
  function Assert-NoReparsePath([string]$path) {
    $item = Get-Item -LiteralPath $path -Force
    while ($null -ne $item) {
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Stien indeholder et reparse point: $($item.FullName)" }
      $parent = if ($item -is [IO.DirectoryInfo]) { $item.Parent } else { $item.Directory }
      if ($null -eq $parent -or $parent.FullName -eq $item.FullName) { break }
      $item = $parent
    }
  }
  Assert-NoReparsePath $data
  Assert-NoReparsePath $markerPath
  try { $owner = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { throw 'Installations-ejerskabet kunne ikke læses.' }
  if ($owner.schemaVersion -ne 1 -or $owner.product -ne 'ElevhubServer' -or $owner.state -ne 'installed' -or
      [IO.Path]::GetFullPath([string]$owner.dataRoot) -ine $data -or [string]::IsNullOrWhiteSpace([string]$owner.installRoot)) {
    throw 'Installations-ejerskabet matcher ikke den valgte datamappe.'
  }
  $installed = [IO.Path]::GetFullPath([string]$owner.installRoot)
  $expected = [IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'ElevhubServer'))
  if ($installed -ine $expected -or -not (Test-Path -LiteralPath $installed -PathType Container)) {
    throw 'Installations-ejerskabet peger ikke på den faste Elevhub-generation.'
  }
  Assert-NoReparsePath $installed
  $runtimePython = Join-Path $installed 'runtime\python.exe'
  if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) { throw "Installeret Elevhub-fil mangler: $runtimePython" }
  Assert-NoReparsePath $runtimePython
  [pscustomobject]@{ DataRoot=$data; InstallRoot=$installed; Owner=$owner }
}

$binding = Get-InstalledBinding $DataRoot
$DataRoot = $binding.DataRoot
$PackageRoot = $binding.InstallRoot
$owner = $binding.Owner
$python = Join-Path $PackageRoot 'runtime\python.exe'; $cli = Join-Path $PackageRoot 'deployment\school-setup\enrollment.py'
$startupMode = 'legacy'; $startup = $null; $installationId = $null
$startupScript = Join-Path $PackageRoot 'server\generation-startup.ps1'
if (Test-Path -LiteralPath $startupScript -PathType Leaf) {
  . $startupScript
  $startupMode = Get-InstallationStartupMode -Owner $owner -InstallRoot $PackageRoot
} elseif ($null -ne $owner.PSObject.Properties['startupProtocol']) {
  throw 'Installationen kræver generation-start, men startup-helperen mangler.'
}
if ($startupMode -eq 'generation-v1') {
  $installationId = Get-GenerationInstallationId -Owner $owner
} elseif ($startupMode -ne 'legacy') {
  throw "Ukendt opstartsprotokol: $startupMode"
} elseif (@(Get-ChildItem -LiteralPath $PackageRoot -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'control' }).Count -gt 0) {
  throw 'En legacy-installation må ikke have en generationskontrolindgang.'
} elseif (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {
  throw "Installeret Elevhub-fil mangler: $cli"
}

function Invoke-GenerationEntry {
  param([string]$Entry, [string[]]$Arguments, [switch]$Json)
  $script:startup = Get-GenerationCommand -InstallRoot $PackageRoot -InstallationId $installationId -Entry $Entry -Arguments $Arguments
  if (-not $script:startup.Execute -or -not $script:startup.ArgumentList -or -not $script:startup.WorkingDirectory) { throw 'Generationsstarten returnerede en ufuldstændig kommando.' }
  $oldLocation = Get-Location
  try {
    Set-Location -LiteralPath ([string]$script:startup.WorkingDirectory)
    $output = & $script:startup.Execute @($script:startup.ArgumentList) 2>&1
  } finally { Set-Location $oldLocation }
  if ($LASTEXITCODE -ne 0) { throw "Elevhub-handlingen fejlede: $($output -join ' ')" }
  $text = ($output -join "`n").Trim()
  if ($Json) {
    if (-not $text) { throw 'Elevhub-handlingen returnerede ikke et resultat.' }
    try { return ($text | ConvertFrom-Json) } catch { throw 'Elevhub-handlingen returnerede ugyldig JSON.' }
  }
  return $text
}

function Invoke-SchoolAdmin {
  param([string]$Action, [string[]]$Arguments = @())
  return Invoke-GenerationEntry -Entry 'school-admin' -Arguments (@($Action) + $Arguments) -Json
}

function Invoke-SchoolBackup {
  param([ValidateSet('create','verify','restore')][string]$Action, [string]$Source, [string]$Destination)
  $arguments = @()
  if ($Source) { $arguments += @('--source', $Source) }
  if ($Destination) { $arguments += @('--destination', $Destination) }
  if ($startupMode -eq 'generation-v1') {
    return Invoke-SchoolAdmin -Action ('backup-' + $Action) -Arguments $arguments
  }
  $oldLocation = Get-Location
  try {
    Set-Location -LiteralPath $PackageRoot
    $output = & $python -B -m app.school_backup $Action --runtime-root $PackageRoot --data-root $DataRoot @arguments 2>&1
  } finally { Set-Location $oldLocation }
  $exitCode = $LASTEXITCODE
  try { $result = ($output -join "`n") | ConvertFrom-Json } catch { throw 'Backupværktøjet returnerede ikke et læsbart resultat.' }
  if ($exitCode -ne 0 -or $result.ok -ne $true) { throw ('Backuphandlingen fejlede: ' + $result.error) }
  return $result
}

function Read-BackupPath([string]$Prompt) {
  $value = Read-Host $Prompt
  if ([string]::IsNullOrWhiteSpace($value)) { throw 'Der blev ikke valgt en mappe.' }
  if (-not [IO.Path]::IsPathRooted($value) -or $value.StartsWith('\\')) { throw 'Vælg en absolut sti på et lokalt drev.' }
  return [IO.Path]::GetFullPath($value)
}

function Invoke-BackupMenu([string]$Choice) {
  if ($Choice -eq '9') {
    Write-Host 'Gem arbejdet og stop server, tilmelding og lokal laserstation før backup. Backupen afviser kørende tjenester og starter dem ikke igen.'
    Write-Host 'Backupen indeholder skolens private nøgler og elevdata. Vælg en beskyttet lokal mappe; kopier den derefter til skolens backupmedie.'
    $destination = Read-BackupPath 'Ny backupmappe (forældremappen skal findes)'
    $result = Invoke-SchoolBackup -Action create -Destination $destination
    Write-Host ('Backup oprettet og kontrolleret: ' + $result.folder)
  } elseif ($Choice -eq '10') {
    $source = Read-BackupPath 'Backupmappe, som skal kontrolleres'
    $result = Invoke-SchoolBackup -Action verify -Source $source
    Write-Host ('Backupens filer og databaser er kontrolleret: ' + $result.folder)
  } elseif ($Choice -eq '11') {
    $source = Read-BackupPath 'Backupmappe, som skal gendannes'
    $destination = Read-BackupPath 'Ny mappe til en isoleret gendannelsesprøve'
    $result = Invoke-SchoolBackup -Action restore -Source $source -Destination $destination
    Write-Host ('Gendannelsesprøven er oprettet: ' + $result.folder)
    Write-Host 'Kopien er spærret for opstart, og printkøen er pauset. Den aktive installation er ikke erstattet. Bevar holdmarkørerne; ibrugtagning kræver særskilt kontrol.'
  } else { throw 'Ukendt backupvalg.' }
}

function Invoke-Enrollment {
  param([string]$Action, [string[]]$Arguments)
  if ($startupMode -eq 'generation-v1') {
    return Invoke-GenerationEntry -Entry 'enrollment' -Arguments (@($Action) + $Arguments)
  }
  & $python -B $cli $Action --data-root $DataRoot @Arguments
  if ($LASTEXITCODE -ne 0) { throw "Skolehandlingen fejlede med kode $LASTEXITCODE." }
}

function Invoke-TlsRenewal {
  # TLS renewal is generation-v1 only.  The Python operator validates the
  # selected generation and task ownership before using the shared controller;
  # legacy has no safe selection contract and is rejected before any stop.
  if ($startupMode -ne 'generation-v1') { throw 'TLS-fornyelse kræver en generation-v1 installation.' }
  $oldLocation = Get-Location
  try {
    Set-Location -LiteralPath $PackageRoot
    $output = & $python -B -I -m app.school_tls renew --runtime-root $PackageRoot 2>&1
    $exitCode = $LASTEXITCODE
  } finally { Set-Location $oldLocation }
  if ($exitCode -ne 0) { throw ('TLS-fornyelsen fejlede: ' + ($output -join ' ')) }
  try {
    $result = ($output -join "`n").Trim() | ConvertFrom-Json
    if (-not $result.ok) { throw [Exception]([string]$result.error) }
  } catch { throw ('TLS-fornyelsen returnerede ikke et gyldigt resultat: ' + $_.Exception.Message) }
  Write-Host 'Servercertifikatet er fornyet med samme CA og servernøgle.'
}

function Open-LocalUrl {
  param([object]$Value, [switch]$Teacher)
  if (-not ($Value -is [string])) { throw 'Skoleadministrationen returnerede en ugyldig adresse.' }
  $uri = $null
  if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -notin @('http','https')) { throw 'Skoleadministrationen returnerede en ugyldig adresse.' }
  $ip = $null
  if (-not [Net.IPAddress]::TryParse($uri.Host, [ref]$ip)) { throw 'Skoleadministrationen returnerede en ikke-lokal adresse.' }
  if ($Teacher) {
    if ($ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw 'Læreradressen skal være en lokal IPv4-adresse.' }
  } elseif (-not [Net.IPAddress]::IsLoopback($ip)) { throw 'Skoleadministrationen returnerede en ikke-lokal adresse.' }
  Start-Process $uri.AbsoluteUri
}

function Get-AdminSnapshot {
  if ($startupMode -eq 'generation-v1') { return Invoke-SchoolAdmin 'inspect' }
  return $null
}

if ($startupMode -eq 'generation-v1') {
  $probe = Get-AdminSnapshot
  if ([IO.Path]::GetFullPath([string]$probe.runtime_root) -ine $PackageRoot -or
      [string]$probe.installation_id -ine $installationId) { throw 'Den aktive generationsmarkør matcher ikke skoleadministrationen.' }
}
if ($ValidateOnly) {
  if ($startupMode -eq 'generation-v1') {
    [ordered]@{dataRoot=$probe.data_root; programRoot=$probe.program_root; installRoot=$PackageRoot; protocol='generation-v1'; revision=$probe.revision} | ConvertTo-Json -Compress
  } else { [ordered]@{dataRoot=$DataRoot; installRoot=$PackageRoot} | ConvertTo-Json -Compress }
  exit 0
}
while ($true) {
Write-Host 'Makerværkstedet — skoleadministration'
Write-Host '1. Opret engangskode til en ny elevcomputer'
Write-Host '2. Åbn SSH til en registreret computer'
Write-Host '3. Kontrollér opsætning'
Write-Host '4. Kopiér offentlig skolefil til USB'
Write-Host '5. Vis lokale lærer-/printkoder'
Write-Host '6. Åbn lokale lærerpaneler'
Write-Host '7. Afmeld computer før geninstallation'
Write-Host '8. Åbn serverens kontrolpanel (printere, computere og versioner)'
Write-Host '9. Opret og kontrollér offline backup'
Write-Host '10. Kontrollér en eksisterende backup'
Write-Host '11. Gendan backup til en isoleret prøve'
Write-Host '12. Forny servercertifikat (stop serveren midlertidigt)'
Write-Host '0. Afslut'
$choice = Read-Host 'Vælg'
try {
switch ($choice) {
  '0' { exit 0 }
  '1' { $name = Read-Host 'Computernavn'; Invoke-Enrollment 'ticket' @('--name', $name) }
  '2' { $name = Read-Host 'Computernavn'; Invoke-Enrollment 'ssh' @('--name', $name) }
  '3' { Invoke-Enrollment 'check' @() }
  '4' {
    $destination = Read-Host 'Angiv USB-mappe'
    if (-not (Test-Path -LiteralPath $destination -PathType Container)) { throw 'USB-mappen findes ikke.' }
    $target = Join-Path $destination 'school-connection.json'
    $fresh = Join-Path $env:TEMP ('school-connection-' + [Guid]::NewGuid().ToString('N') + '.json')
    try {
      Invoke-Enrollment 'connection' @('--output', $fresh)
      if (Test-Path -LiteralPath $target) {
        if (-not (Test-Path -LiteralPath $target -PathType Leaf) -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -cne (Get-FileHash -LiteralPath $fresh -Algorithm SHA256).Hash) {
          throw 'USB-mappen indeholder en anden school-connection.json. Vælg en ny tom mappe; den eksisterende fil ændres ikke.'
        }
        Write-Host "Skolens aktuelle forbindelse findes allerede: $target"
      } else {
        $stream = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $bytes = [IO.File]::ReadAllBytes($fresh); $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
        Write-Host "Kopieret aktuel offentlig skolefil: $target"
      }
    } finally { if (Test-Path -LiteralPath $fresh) { Remove-Item -LiteralPath $fresh -Force } }
  }
  '5' {
    if ($startupMode -eq 'generation-v1') {
      $codes = Invoke-SchoolAdmin 'codes'
      foreach ($item in @($codes)) { Write-Host (($item.label) + ': ' + ($(if ($null -eq $item.code) { 'koden er endnu ikke oprettet.' } else { $item.code }))) }
    } else {
      foreach ($item in @(@{label='Printværksted';path=(Join-Path $DataRoot 'printvaerksted\laererkode.txt')}, @{label='Laserværksted';path=(Join-Path $DataRoot 'designvaerksted\laererkode.txt')})) {
        if (Test-Path -LiteralPath $item.path -PathType Leaf) { Write-Host ($item.label + ': ' + (Get-Content -LiteralPath $item.path -Raw).Trim()) } else { Write-Host ($item.label + ': koden er endnu ikke oprettet.') }
      }
    }
  }
  '6' {
    if ($startupMode -eq 'generation-v1') {
      $urls = Invoke-SchoolAdmin 'urls'
      if ($urls.PSObject.Properties.Name -contains 'teacher' -and $null -ne $urls.teacher) { Open-LocalUrl $urls.teacher -Teacher }
      foreach ($key in @('design','laser')) { if ($urls.PSObject.Properties.Name -contains $key -and $null -ne $urls.$key) { Open-LocalUrl $urls.$key } }
    } else {
      & (Join-Path $PSScriptRoot 'Open-ControlPanel.ps1')
      $teacherBackendPort = if ($owner.PSObject.Properties.Name -contains 'teacherBackendPort') { [int]$owner.teacherBackendPort } else { 8767 }
      Start-Process ('http://127.0.0.1:' + $teacherBackendPort)
      $school = Get-Content -LiteralPath (Join-Path $DataRoot 'school.json') -Raw | ConvertFrom-Json
      if ($school.role -eq 'makerspace') { Start-Process 'http://127.0.0.1:8790/' }
    }
  }
  '7' {
    $name = Read-Host 'Computernavn'; $confirm = Read-Host "Skriv computernavnet igen for at bekræfte: $name"
    if ($confirm -ne $name) { throw 'Bekræftelsen matchede ikke.' }
    Invoke-Enrollment 'revoke' @('--name', $name, '--confirm-name', $confirm)
  }
  '8' {
    if ($startupMode -eq 'generation-v1') { $urls = Invoke-SchoolAdmin 'urls'; Open-LocalUrl $urls.controlpanel -Teacher }
    else { & (Join-Path $PSScriptRoot 'Open-ControlPanel.ps1') }
  }
  '9' { Invoke-BackupMenu $choice }
  '10' { Invoke-BackupMenu $choice }
  '11' { Invoke-BackupMenu $choice }
  '12' { Invoke-TlsRenewal }
  default { throw 'Ugyldigt valg.' }
}
if ($LASTEXITCODE -ne 0) { throw "Skolehandlingen fejlede med kode $LASTEXITCODE." }
} catch {
  Write-Host ('Handlingen blev ikke gennemført: ' + $_.Exception.Message)
  if ($choice -in @('9','10','11')) {
    Write-Host 'Ved et afbrudt backup- eller gendannelsesforsøg bevares den nye mappe til eftersyn; vælg en anden ny destination ved næste forsøg.'
  }
  $global:LASTEXITCODE = 0
}
Read-Host 'Tryk Enter for at vende tilbage til menuen' | Out-Null
}
