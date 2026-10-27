param(
  [ValidateSet('update','restore','rollback','resume','cancel','status')][string]$Action,
  [string]$RuntimeRoot = "$env:ProgramFiles\ElevhubServer",
  [string]$Folder,
  [string]$MaterializedFolder,
  [string]$ProgramRoot,
  [string]$BackupSource,
  [string]$CompletedFolder,
  [string]$SettledFolder
)
$ErrorActionPreference = 'Stop'
$global:LASTEXITCODE = 0

function Assert-PlainPath([string]$Path, [switch]$File) {
  if (-not [IO.Path]::IsPathRooted($Path) -or $Path.StartsWith('\\')) { throw 'Der kræves en lokal absolut sti.' }
  $item = Get-Item -LiteralPath $Path -Force
  if ($File -and $item.PSIsContainer) { throw "Fil mangler: $Path" }
  if (-not $File -and -not $item.PSIsContainer) { throw "Mappe mangler: $Path" }
  while ($null -ne $item) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Stien indeholder et reparse point.' }
    $item = if ($item -is [IO.DirectoryInfo]) { $item.Parent } else { $item.Directory }
  }
}

function Quote-WindowsArgument([string]$Value) {
  # Win32 argv quoting, including embedded quotes and trailing backslashes.
  $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
  $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
  return '"' + $escaped + '"'
}

try {
  if (-not $Action) {
    Write-Host 'Makerværkstedet: vedligeholdelse'
    Write-Host 'Kreditering: Klostermarksskolen.'
    Write-Host '1 Opdater  2 Gendan backup  3 Tilbagefør program  4 Genoptag  5 Annuller  6 Status'
    $choice = Read-Host 'Vælg handling'
    $actions = @{'1'='update';'2'='restore';'3'='rollback';'4'='resume';'5'='cancel';'6'='status'}
    if (-not $actions.ContainsKey($choice)) { throw 'Ukendt handling.' }
    $Action = $actions[$choice]
  }
  if (-not $Folder) { $Folder = Read-Host 'Klargørings-/operationsmappe (ny mappe ved nyt forsøg)' }
  if ($Action -in @('update','restore','rollback')) {
    if (-not $MaterializedFolder) { $MaterializedFolder = Read-Host 'Ny permanent generationsmappe' }
    if ($Action -in @('update','restore') -and -not $ProgramRoot) { $ProgramRoot = Read-Host 'Kontrolleret programkandidat med makerspace-release.json' }
    if ($Action -eq 'restore' -and -not $BackupSource) { $BackupSource = Read-Host 'Samlet backupmappe' }
    if ($Action -eq 'rollback' -and -not $CompletedFolder) { $CompletedFolder = Read-Host 'Operationsmappe for den afsluttede opdatering' }
  }
  Assert-PlainPath $RuntimeRoot
  $RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
  $python = Join-Path $RuntimeRoot 'runtime\python.exe'
  Assert-PlainPath $python -File
  Assert-PlainPath (Join-Path $RuntimeRoot 'app\school_maintenance.py') -File
  Assert-PlainPath (Join-Path $RuntimeRoot 'control')

  $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if ($Action -ne 'status' -and -not $isAdmin) {
    $elevationArguments = @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',$PSCommandPath,'-Action',$Action,'-RuntimeRoot',$RuntimeRoot,'-Folder',$Folder)
    foreach ($pair in @(@('-MaterializedFolder',$MaterializedFolder),@('-ProgramRoot',$ProgramRoot),@('-BackupSource',$BackupSource),@('-CompletedFolder',$CompletedFolder),@('-SettledFolder',$SettledFolder))) {
      if ($pair[1]) { $elevationArguments += @([string]$pair[0],[string]$pair[1]) }
    }
    $quotedArguments = @($elevationArguments | ForEach-Object { Quote-WindowsArgument ([string]$_) })
    $elevated = Start-Process powershell.exe -Verb RunAs -ArgumentList $quotedArguments -WindowStyle Hidden -Wait -PassThru
    # The elevated window is hidden. Read its durable outcome back in this
    # original window; status uses no writable lock or service operation.
    $previous = Get-Location
    try {
      Set-Location -LiteralPath $RuntimeRoot
      & $python -B -I -m app.school_maintenance status --runtime-root $RuntimeRoot --folder $Folder
    } finally { Set-Location $previous }
    if ($elevated.ExitCode -ne 0) { Write-Warning 'Vedligeholdelsen fejlede. Den gemte operation kan undersøges, genoptages eller annulleres.' }
    exit $elevated.ExitCode
  }

  # Direct fixed operator: never use Invoke-GenerationEntry here. Its lifetime
  # lease would prevent the exclusive update/restore/rollback lock.
  $arguments = @('-B','-I','-m','app.school_maintenance',$Action,'--runtime-root',$RuntimeRoot,'--folder',$Folder)
  foreach ($pair in @(@('--materialized-folder',$MaterializedFolder),@('--program-root',$ProgramRoot),@('--backup-source',$BackupSource),@('--completed-folder',$CompletedFolder),@('--settled-folder',$SettledFolder))) {
    if ($pair[1]) { $arguments += @([string]$pair[0],[string]$pair[1]) }
  }
  $previous = Get-Location
  try {
    Set-Location -LiteralPath $RuntimeRoot
    & $python @arguments
    $result = $LASTEXITCODE
  } finally { Set-Location $previous }
  exit $result
} catch {
  Write-Error $_ -ErrorAction Continue
  exit 1
}
