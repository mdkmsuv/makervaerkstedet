# Native installer hooks. Only the fixed server updater's code/task are touched.
function Get-ServerUpdateLifecyclePaths {
 $program=Join-Path $env:ProgramFiles 'ElevhubServer'
 $store=Join-Path $env:ProgramData 'MakervaerkstedetServerUpdate'
 return @{Store=$store;Helper=(Join-Path $store 'worker.ps1');InstalledHelper=(Join-Path $program 'server\server-update.ps1');Task='Makervaerkstedet-ServerUpdate'}
}
function Assert-ServerUpdateLifecyclePlain([string]$Path) {
 if(-not [IO.Path]::IsPathRooted($Path) -or $Path.StartsWith('\\')){throw 'Local absolute updater path required'}
 $cursor=[IO.Path]::GetFullPath($Path)
 while($cursor){
  if((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Linked updater path refused'}
  $cursor=Split-Path $cursor -Parent
 }
}
function Invoke-ServerUpdateBridgeCommand([string]$Helper,[string]$Mode) {
 Assert-ServerUpdateLifecyclePlain $Helper
 $power=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
 $output=& $power -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Helper -Mode $Mode
 if($LASTEXITCODE -ne 0){throw 'Server update bridge could not be configured'}
 $result=($output -join "`n")|ConvertFrom-Json
 if($Mode -eq 'Remove'){
  if($result.schema -ne 1 -or $result.removed -isnot [bool]){throw 'Server update bridge did not confirm operation'}
  return $result
 }
 $field=@{Install='installed';Remove='removed';Status='available'}[$Mode]
 if($result.$field -ne $true){throw 'Server update bridge did not confirm operation'}
 return $result
}
function Enable-ServerUpdateBridge([string]$Journal) {
 $paths=Get-ServerUpdateLifecyclePaths
 foreach($path in @($Journal,$paths.Store,$paths.Helper)){Assert-ServerUpdateLifecyclePlain $path}
 $saved=Join-Path $Journal 'server-update-bridge-before.json'
 if(Test-Path -LiteralPath $saved){throw 'Server update bridge transaction already exists'}
 $task=Get-ScheduledTask -TaskName $paths.Task -ErrorAction SilentlyContinue
 $hash=$null
 if($null -ne $task){
  $null=Invoke-ServerUpdateBridgeCommand $paths.InstalledHelper 'Status'
  $hash=(Get-FileHash -LiteralPath $paths.Helper).Hash
  Copy-Item -LiteralPath $paths.Helper -Destination (Join-Path $Journal 'server-update-worker-before.ps1')
 }
 @{schema=1;had_task=($null -ne $task);helper_sha256=$hash}|ConvertTo-Json|Set-Content -LiteralPath $saved -Encoding UTF8
 $null=Invoke-ServerUpdateBridgeCommand $paths.InstalledHelper 'Install'
}
function Restore-ServerUpdateBridge([string]$Journal,[string]$FallbackHelper) {
 $paths=Get-ServerUpdateLifecyclePaths
 $saved=Join-Path $Journal 'server-update-bridge-before.json'
 Assert-ServerUpdateLifecyclePlain $saved
 if(-not (Test-Path -LiteralPath $saved)){return}
 $before=Get-Content -LiteralPath $saved -Raw -Encoding UTF8|ConvertFrom-Json
 if($before.schema -ne 1 -or $before.had_task -isnot [bool]){throw 'Invalid server updater rollback record'}
 if($before.had_task){
  $backup=Join-Path $Journal 'server-update-worker-before.ps1'
  foreach($path in @($backup,$paths.Helper)){Assert-ServerUpdateLifecyclePlain $path}
  if($before.helper_sha256 -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash -LiteralPath $backup).Hash -ne $before.helper_sha256){throw 'Server updater rollback hash differs'}
  $temporary=Join-Path $paths.Store ([Guid]::NewGuid().ToString('N')+'.restore')
  try {Copy-Item -LiteralPath $backup -Destination $temporary;[IO.File]::Replace($temporary,$paths.Helper,[NullString]::Value)}
  finally {if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary}}
 }elseif($null -ne (Get-ScheduledTask -TaskName $paths.Task -ErrorAction SilentlyContinue)){
  $null=Invoke-ServerUpdateBridgeCommand $FallbackHelper 'Remove'
 }
}
