[CmdletBinding()]
param([ValidateSet('Enable','Remove')][string]$Mode='Enable')
$ErrorActionPreference='Stop'
$root=Join-Path $env:ProgramFiles 'ElevhubServer'
$helper=Join-Path $root 'server\server-restart.ps1'
$powershell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$desktop=[Environment]::GetFolderPath('CommonDesktopDirectory')
if(-not $desktop){throw 'Serverens fælles skrivebord kunne ikke findes.'}
$shortcutPath=Join-Path $desktop 'Genstart Makerværkstedets server.lnk'
$arguments='-NoLogo -NoProfile -ExecutionPolicy Bypass -File "'+$helper+'" -Mode Local'
foreach($path in @($root,$helper,$shortcutPath)){
 $cursor=$path
 while($cursor){
  if(Test-Path -LiteralPath $cursor){if((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Genstartsopsætningen afviser omdirigerede stier.'}}
  $cursor=Split-Path $cursor -Parent
 }
}
if(-not(Test-Path -LiteralPath $helper -PathType Leaf)){throw 'Serverens genstartsværktøj mangler.'}
$shell=New-Object -ComObject WScript.Shell
$exists=Test-Path -LiteralPath $shortcutPath
if($exists){
 $link=$shell.CreateShortcut($shortcutPath)
 if($link.TargetPath -ine $powershell -or $link.Arguments -cne $arguments -or $link.WorkingDirectory -ine $root){throw 'Den eksisterende genstartsgenvej er ændret; den overtages ikke.'}
}
$hostMode=if($Mode -eq 'Enable'){'Install'}else{'Remove'}
$result=& $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $helper -Mode $hostMode
if($LASTEXITCODE -ne 0){throw ('Genstartsopsætningen fejlede: '+($result -join ' '))}
if($Mode -eq 'Enable'){
 if(-not $exists){
  $link=$shell.CreateShortcut($shortcutPath)
  $link.TargetPath=$powershell;$link.Arguments=$arguments;$link.WorkingDirectory=$root
  $link.Description='Genstart hele Windows-serveren efter bekræftelse, også hvis Makerværkstedets webside er nede.'
  $link.Save()
 }
}elseif($exists){Remove-Item -LiteralPath $shortcutPath -Force}
$result
