param([string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) ('output\setup-wizard-preview-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))))
# Render our own WPF controls without showing a window or starting installation.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'deployment\school-setup\setup-wizard-model.ps1')
. (Join-Path $root 'deployment\school-setup\setup-wizard-view.ps1')
. (Join-Path $root 'deployment\school-setup\setup-wizard-runner.ps1')
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$view = Initialize-SetupWizardView -PackageRoot $root -Networks @(
    @{alias='Ethernet';address='192.168.10.20';prefixLength=24;subnet='192.168.10.0/24';dhcp=$true;gateway='192.168.10.1'},
    @{alias='Wi-Fi';address='192.0.2.42';prefixLength=24;subnet='192.0.2.0/24';dhcp=$true;gateway='192.0.2.1'}
) -OnValidate {param($values) Test-SetupWizardInput $values} -OnStart {throw 'Rendering must never start installation'} -OnPoll {throw 'Rendering must never poll installation'} -OnChecklist {param($values) Get-SetupItChecklist $values}

function Save-WizardPreview([string]$Name, [int]$Width, [int]$Height) {
    $visual = $view.Window.Content
    # Window.Background is outside the offscreen tree; make the canvas explicit.
    $visual.Background = [Windows.Media.Brushes]::White
    $visual.Measure([Windows.Size]::new($Width,$Height))
    $visual.Arrange([Windows.Rect]::new(0,0,$Width,$Height))
    $visual.UpdateLayout()
    $bitmap = [Windows.Media.Imaging.RenderTargetBitmap]::new($Width,$Height,96,96,[Windows.Media.PixelFormats]::Pbgra32)
    $bitmap.Render($visual)
    $encoder = [Windows.Media.Imaging.PngBitmapEncoder]::new()
    $encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
    $path = Join-Path $OutputDirectory ($Name + '.png')
    $stream = [IO.File]::Create($path)
    try {$encoder.Save($stream)} finally {$stream.Dispose()}
    [pscustomobject]@{file=$Name+'.png';width=$Width;height=$Height;step=$view.Step;scrollableHeight=$view.Controls.PageScroll.ScrollableHeight}
}

$results = @()
$results += Save-WizardPreview '01-vaelg-opsaetning' 1008 720
$view.Controls.SeparateComputers.IsChecked = $true
$results += Save-WizardPreview '01-delt-lille-skaerm' 736 540
$view.Controls.SchoolName.Text = 'Søndervang Skole'
& $view.Render 2
$results += Save-WizardPreview '02-skolen' 1008 720
$view.Controls.NetworkCandidate.SelectedIndex = 0
& $view.Render 3
$results += Save-WizardPreview '03-netvaerk' 1008 720
$results += Save-WizardPreview '03-netvaerk-lille-skaerm' 736 540
$view.Controls.NetworkHelp.IsExpanded = $true
$view.Controls.NetworkHelp.BringIntoView()
$results += Save-WizardPreview '03-hjaelp-i-vinduet' 1008 720
$view.Controls.NetworkHelp.IsExpanded = $false
$view.Controls.AddressConfirmed.IsChecked = $true
$view.Plan = Test-SetupWizardInput (& $view.Read)
& $view.Render 4
$results += Save-WizardPreview '04-gennemgaa' 1008 720
$view.Controls.StationComponent.IsChecked = $true
& $view.Render 2
$results += Save-WizardPreview '02-lasercomputer' 1008 720
$view.Plan = @{connectionSummary=@{schoolName='Søndervang Skole';serverUrl='https://192.168.10.20:8780';stationId='laser-fbb2';machine='fbb2'}}
$view.Controls.ConnectionFile.Text = 'E:\laser-station-connection.json'
& $view.Render 4
$results += Save-WizardPreview '04-lasercomputer' 1008 720
& $view.Render 5
$fixturePlan = @{entry='Setup-LaserStation.ps1';values=@{deployment='separate-computers'};parameters=@{PackageRoot='C:\Makervaerkstedet-pakke';Action='Install';NoStart=$true}}
$fixtureStatus = @{state='failed';phase='installation-failed';message='Eksempel: Installationen blev afbrudt.';exitCode=1;log='Layoutprøve. Ingen installation er startet.'}
$help = Get-SetupWizardGuidance $fixturePlan $fixtureStatus
& $view.SetGuidance $help.guidanceTitle $help.guidanceText $help.technicalHelp
& $view.Finish 'failed' 'Eksempel: Installationen kunne ikke afsluttes.' ''
$view.Controls.InstallLog.Text = "Layoutprøve med syntetiske oplysninger.`nIngen installation eller Windows-ændring."
$results += Save-WizardPreview '05-fejl' 1008 720
$results += Save-WizardPreview '05-fejl-lille-skaerm' 736 540
$fixtureStatus = @{state='succeeded';phase='checked';exitCode=0;checks=@(@{name='fixture';ok=$true;message='Kun layoutprøve'})}
$help = Get-SetupWizardGuidance $fixturePlan $fixtureStatus
& $view.SetGuidance $help.guidanceTitle $help.guidanceText $help.technicalHelp
& $view.Finish 'succeeded' 'Eksempel: Installation og lokal efterkontrol er gennemført.' ''
$view.Controls.PageScroll.ScrollToTop()
$results += Save-WizardPreview '05-naeste-trin' 1008 720
$view.Timer.Stop()
$view.Window.Close()
$results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'layout.json') -Encoding UTF8
$results
