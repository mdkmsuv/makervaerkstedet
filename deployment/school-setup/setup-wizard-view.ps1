# The view has no installer, network or process authority. Those operations are
# supplied by the caller through the explicit validation/start/poll callbacks.
function Get-SetupWizardValue {
    param([object]$Object, [string]$Name, [object]$Default = $null)
    if ($null -eq $Object) { return $Default }
    if ($Object -is [Collections.IDictionary]) {
        if ($Object.Contains($Name)) { return $Object[$Name] }
    } elseif ($null -ne $Object.PSObject.Properties[$Name]) { return $Object.$Name }
    return $Default
}

function New-SetupWizardView {
    [CmdletBinding()]
    param([string]$PackageRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
    Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
    $xamlPath = Join-Path $PSScriptRoot 'Setup-Wizard.xaml'
    $reader = [Xml.XmlReader]::Create($xamlPath)
    try { $window = [Windows.Markup.XamlReader]::Load($reader) } finally { $reader.Dispose() }
    $fontPath = Join-Path $PackageRoot 'web\assets\metrophobic-latin.ttf'
    if (Test-Path -LiteralPath $fontPath -PathType Leaf) {
        $fontDirectory = [Uri]::new(([IO.Path]::GetFullPath((Split-Path -Parent $fontPath)) + [IO.Path]::DirectorySeparatorChar))
        $window.FontFamily = [Windows.Media.FontFamily]::new($fontDirectory, './#Metrophobic')
    }
    $controls = @{}
    $document = [xml][IO.File]::ReadAllText($xamlPath)
    $manager = [Xml.XmlNamespaceManager]::new($document.NameTable)
    $manager.AddNamespace('x', 'http://schemas.microsoft.com/winfx/2006/xaml')
    foreach ($node in $document.SelectNodes('//*[@x:Name]', $manager)) {
        $name = $node.GetAttribute('Name', 'http://schemas.microsoft.com/winfx/2006/xaml')
        $controls[$name] = $window.FindName($name)
    }
    [pscustomobject]@{ Window = $window; Controls = $controls }
}

function Initialize-SetupWizardView {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$PackageRoot,
        [object[]]$Networks = @(),
        [Parameter(Mandatory=$true)][scriptblock]$OnValidate,
        [Parameter(Mandatory=$true)][scriptblock]$OnStart,
        [Parameter(Mandatory=$true)][scriptblock]$OnPoll,
        [Parameter(Mandatory=$true)][scriptblock]$OnChecklist,
        [scriptblock]$OnCopy = { param([string]$Text) [Windows.Clipboard]::SetText($Text) }
    )
    $view = New-SetupWizardView -PackageRoot $PackageRoot
    $context = @{
        Window = $view.Window; Controls = $view.Controls; PackageRoot = $PackageRoot
        Step = 1; Running = $false; Handle = $null; Plan = $null; Timer = $null; ProvidedGuidance = $false
        OnValidate = $OnValidate; OnStart = $OnStart; OnPoll = $OnPoll; OnChecklist = $OnChecklist; OnCopy = $OnCopy
    }
    $context.Read = {
        $c = $context.Controls
        $separate = [bool]$c.SeparateComputers.IsChecked
        $station = $separate -and [bool]$c.StationComponent.IsChecked
        @{
            deployment = $(if ($separate) { 'separate-computers' } else { 'single-computer' })
            component = $(if ($station) { 'laser-station' } else { 'server' })
            schoolName = $c.SchoolName.Text.Trim(); roleIp = $c.RoleIp.Text.Trim()
            studentSubnet = $c.StudentSubnet.Text.Trim(); theme = [string]$c.Theme.SelectedItem.Tag
            machine = [string]$c.Machine.SelectedItem.Tag; addressConfirmed = [bool]$c.AddressConfirmed.IsChecked
            connectionFile = $c.ConnectionFile.Text.Trim(); packageRoot = $context.PackageRoot
        }
    }.GetNewClosure()
    $context.ClearErrors = {
        $context.Controls.ErrorPanel.Visibility = 'Collapsed'
        $context.Controls.ErrorText.Text = ''
    }.GetNewClosure()
    $context.Errors = {
        param([object[]]$Errors)
        $messages = @($Errors | ForEach-Object { [string](Get-SetupWizardValue $_ 'message' ([string]$_)) })
        $context.Controls.ErrorText.Text = ($messages -join [Environment]::NewLine)
        $context.Controls.ErrorPanel.Visibility = 'Visible'
        $map = @{ schoolName='SchoolName'; roleIp='RoleIp'; studentSubnet='StudentSubnet'; theme='Theme'; machine='Machine'; addressConfirmed='AddressConfirmed'; connectionFile='ConnectionFile'; deployment='SingleComputer'; component='ServerComponent' }
        if ($Errors.Count -gt 0) {
            $field = [string](Get-SetupWizardValue $Errors[0] 'field' '')
            if ($map.ContainsKey($field)) { $control = $context.Controls[$map[$field]]; $control.BringIntoView(); [void]$control.Focus() }
        }
    }.GetNewClosure()
    $context.Render = {
        param([int]$Step, [switch]$KeepFocus)
        $context.Step = $Step
        $c = $context.Controls
        $values = & $context.Read
        $station = $values.component -eq 'laser-station'
        for ($i=1; $i -le 5; $i++) {
            $c["Step$i"].Visibility = $(if ($i -eq $Step) { 'Visible' } else { 'Collapsed' })
            $c["StepLabel$i"].Foreground = $(if ($i -eq $Step) { '#b4121a' } else { '#666666' })
            $c["StepLabel$i"].FontWeight = $(if ($i -eq $Step) { 'Bold' } else { 'Normal' })
        }
        $c.StepLabel2.Text = $(if ($station) { '2  Forbindelse' } else { '2  Skolen' })
        $c.StepLabel3.Text = $(if ($station) { '—  Fra serveren' } else { '3  Netværk' })
        $c.StepCaption.Text = $(if ($station) { "Trin $Step af 5 · netværk hentes fra filen" } else { "Trin $Step af 5" })
        $c.SeparateComponentPanel.Visibility = $(if ($values.deployment -eq 'separate-computers') { 'Visible' } else { 'Collapsed' })
        $c.ServerSchoolPanel.Visibility = $(if ($station) { 'Collapsed' } else { 'Visible' })
        $c.StationSchoolPanel.Visibility = $(if ($station) { 'Visible' } else { 'Collapsed' })
        $c.SchoolHeading.Text = $(if ($station) { 'Forbind lasercomputeren til serveren' } else { 'Skolens oplysninger' })
        $c.BackButton.IsEnabled = $Step -gt 1 -and $Step -lt 5
        $c.BackButton.Visibility = $(if ($Step -eq 5) { 'Collapsed' } else { 'Visible' })
        $c.NextButton.Visibility = $(if ($Step -eq 5) { 'Collapsed' } else { 'Visible' })
        $c.NextButton.Content = $(if ($Step -eq 4) { 'Start _installation' } else { '_Næste' })
        $c.CancelButton.Content = $(if ($Step -eq 5 -and -not $context.Running) { '_Luk guiden' } else { '_Luk' })
        $c.CancelButton.IsEnabled = -not $context.Running
        $c.PageScroll.ScrollToTop()
        if ($Step -eq 4) {
            if ($station) {
                $connection = Get-SetupWizardValue $context.Plan 'connectionSummary' $null
                $connectionSchool = [string](Get-SetupWizardValue $connection 'schoolName' '')
                $connectionServer = [string](Get-SetupWizardValue $connection 'serverUrl' '')
                $connectionStation = [string](Get-SetupWizardValue $connection 'stationId' '')
                $connectionMachine = [string](Get-SetupWizardValue $connection 'machine' '')
                $modelName = switch ($connectionMachine) { 'fbb2' { 'FLUX Beambox II' }; 'fhexa1' { 'FLUX HEXA' }; default { $connectionMachine } }
                $c.ReviewSummary.Text = "Opsætning: Central server og separat lasercomputer`nDenne computer: Laserstation`nSkole: $connectionSchool`nServer: $connectionServer`nLaser: $modelName`nStation: $connectionStation`nForbindelsesfil: $($values.connectionFile)`n`nDen centrale server beholder køen. Kontrollér, at skole og server er de rigtige."
                $c.InstallExplanation.Text = 'Installationen tilføjer laserstationen for den aktuelle Windows-bruger og lærer serverens offentlige certifikat at kende. Start guiden som almindelig bruger. Der installeres ikke en ekstra server.'
            } else {
                $layout = $(if ($values.deployment -eq 'single-computer') { 'Alt på én Windows-computer' } else { 'Central Windows-server / VM med separat lasercomputer' })
                $c.ReviewSummary.Text = "Opsætning: $layout`nSkole: $($values.schoolName)`nLaser: $($c.Machine.SelectedItem.Content)`nUdseende: $($c.Theme.SelectedItem.Content)`nServeradresse: $($values.roleIp)`nVærkstedsnet: $($values.studentSubnet)`nFast adresse / reservation: Bekræftet med IT"
                $c.InstallExplanation.Text = 'Når du vælger Start installation, beder Windows om administratorgodkendelse til serverens installation. Hold computeren tændt, og vent på slutresultatet.'
            }
        }
        $focus = switch ($Step) { 1 { 'SingleComputer' }; 2 { if ($station) { 'ConnectionFile' } else { 'SchoolName' } }; 3 { 'NetworkCandidate' }; 4 { 'NextButton' }; 5 { 'CopyHelp' } }
        if (-not $KeepFocus) { [void]$c[$focus].Focus() }
    }.GetNewClosure()
    $context.Validate = {
        param([string[]]$Fields = @())
        try { $result = & $context.OnValidate (& $context.Read) }
        catch { & $context.Errors @(@{field='';message="Oplysningerne kunne ikke kontrolleres: $($_.Exception.Message)"}); return $null }
        $errors = @((Get-SetupWizardValue $result 'errors' @()))
        if ($Fields.Count -gt 0) { $errors = @($errors | Where-Object { ([string](Get-SetupWizardValue $_ 'field' '')) -in $Fields -or -not (Get-SetupWizardValue $_ 'field' '') }) }
        if ($errors.Count -gt 0) { & $context.Errors $errors; return $null }
        if ($Fields.Count -eq 0 -and -not [bool](Get-SetupWizardValue $result 'valid' $false)) {
            & $context.Errors @(@{field='';message='Oplysningerne er ikke godkendt. Gå tilbage og kontrollér felterne.'}); return $null
        }
        return $result
    }.GetNewClosure()
    $context.SetGuidance = {
        param([string]$Title, [string]$Text, [string]$TechnicalHelp = '')
        $c = $context.Controls
        $c.GuidanceHeading.Text = $(if ($Title) { $Title } else { 'Hjælp til næste skridt' })
        $c.GuidanceText.Text = $Text
        $c.TechnicalHelp.Text = $TechnicalHelp
        $c.TechnicalHelpExpander.Visibility = $(if ($TechnicalHelp) { 'Visible' } else { 'Collapsed' })
        $c.GuidancePanel.Visibility = 'Visible'
        $context.ProvidedGuidance = $true
    }.GetNewClosure()
    $context.HelpText = {
        $c = $context.Controls
        $parts = @('Makerværkstedet — hjælp fra installationen', $c.StatusMessage.Text, $c.GuidanceHeading.Text, $c.GuidanceText.Text)
        if ($c.TechnicalHelp.Text) { $parts += @('Tekniske oplysninger til IT', $c.TechnicalHelp.Text) }
        if ($c.NextSteps.Text -and -not $context.ProvidedGuidance) { $parts += $c.NextSteps.Text }
        return (@($parts | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) -join ([Environment]::NewLine + [Environment]::NewLine))
    }.GetNewClosure()
    $context.Finish = {
        param([string]$State, [string]$Message, [string]$NextSteps = '')
        $context.Running = $false
        $context.Timer.Stop()
        $c = $context.Controls
        $c.InstallProgress.IsIndeterminate = $false
        $c.InstallProgress.Value = $(if ($State -eq 'succeeded') { 100 } else { 0 })
        $c.StatusHeading.Text = $(if ($State -eq 'succeeded') { 'Installationen er afsluttet' } else { 'Installationen blev ikke fuldført' })
        $c.StatusMessage.Text = $Message
        $c.NextSteps.Text = $NextSteps
        $c.NextStepsPanel.Visibility = $(if ($NextSteps -and -not $context.ProvidedGuidance) { 'Visible' } else { 'Collapsed' })
        if (-not $context.ProvidedGuidance) {
            $c.GuidanceHeading.Text = $(if ($State -eq 'succeeded') { 'Kontrollér installationen' } else { 'Få hjælp til at fortsætte' })
            $c.GuidanceText.Text = $(if ($State -eq 'succeeded') { 'Læs de næste trin nedenfor, og kontrollér lærerpanel og forbindelse, før systemet tages i brug.' } else { 'Kopiér hjælp og log til skolens IT. Vent med et nyt installationsforsøg, til årsagen er undersøgt. Bevar den udpakkede pakke og eventuelle data fra forsøget.' })
        }
        $c.CancelButton.IsEnabled = $true
        $c.CancelButton.Content = '_Luk guiden'
    }.GetNewClosure()
    $context.Timer = [Windows.Threading.DispatcherTimer]::new()
    $context.Timer.Interval = [TimeSpan]::FromMilliseconds(650)
    $context.Timer.Add_Tick({
        if (-not $context.Running) { return }
        try {
            $result = & $context.OnPoll $context.Handle
            $message = [string](Get-SetupWizardValue $result 'message' '')
            $log = [string](Get-SetupWizardValue $result 'log' '')
            if ($message) { $context.Controls.StatusMessage.Text = $message }
            if ($log -ne $context.Controls.InstallLog.Text) { $context.Controls.InstallLog.Text = $log; $context.Controls.InstallLog.ScrollToEnd() }
            $guidanceTitle = [string](Get-SetupWizardValue $result 'guidanceTitle' '')
            $guidanceText = [string](Get-SetupWizardValue $result 'guidanceText' '')
            $technicalHelp = [string](Get-SetupWizardValue $result 'technicalHelp' '')
            if ($guidanceTitle -or $guidanceText -or $technicalHelp) { & $context.SetGuidance $guidanceTitle $guidanceText $technicalHelp }
            $state = [string](Get-SetupWizardValue $result 'state' 'running')
            if ($state -in @('succeeded','failed')) {
                if (-not ($guidanceTitle -or $guidanceText -or $technicalHelp)) {
                    $context.ProvidedGuidance = $false
                    $context.Controls.TechnicalHelp.Text = ''
                    $context.Controls.TechnicalHelpExpander.Visibility = 'Collapsed'
                }
                $exitCode = Get-SetupWizardValue $result 'exitCode' $null
                if ($state -eq 'succeeded' -and ($null -eq $exitCode -or [int]$exitCode -ne 0)) {
                    $state = 'failed'; $message = 'Installationen meldte ikke en bekræftet afslutning med kode 0. Læs loggen før et nyt forsøg.'
                    & $context.SetGuidance 'Få resultatet kontrolleret af IT' 'Kopiér hjælp og log til skolens IT. Status og afslutningskode stemmer ikke overens, så installationen er ikke bekræftet. Vent med et nyt forsøg, til IT har kontrolleret resultatet.' "Rapporteret afslutningskode: $exitCode"
                }
                if (-not $message) { $message = $(if ($state -eq 'succeeded') { 'Installationsprogrammet afsluttede uden fejl. Følg de næste trin og kontrollér systemet.' } else { 'Læs loggen for at se, hvad der stoppede installationen.' }) }
                & $context.Finish $state $message ([string](Get-SetupWizardValue $result 'nextSteps' ''))
            } elseif ($state -ne 'running') {
                $context.Controls.StatusMessage.Text = 'Installationsstatus kunne ikke genkendes. Installationen kan stadig køre; guiden prøver igen. Du kan kopiere loggen imens.'
            }
        } catch {
            # A polling failure does not prove the worker stopped. Keep the close
            # guard and retry polling; never turn an unknown process into success.
            $context.Controls.StatusMessage.Text = "Status kunne ikke læses. Installationen kan stadig køre; guiden prøver igen. $($_.Exception.Message)"
        }
    }.GetNewClosure())
    $view.Controls.NextButton.Add_Click({
        & $context.ClearErrors
        $values = & $context.Read
        switch ($context.Step) {
            1 { & $context.Render 2 }
            2 {
                $fields = $(if ($values.component -eq 'laser-station') { @('connectionFile') } else { @('schoolName','theme','machine') })
                $result = & $context.Validate $fields
                if ($null -ne $result) { $context.Plan = $result; & $context.Render $(if ($values.component -eq 'laser-station') { 4 } else { 3 }) }
            }
            3 { $result = & $context.Validate; if ($null -ne $result) { $context.Plan = $result; & $context.Render 4 } }
            4 {
                $plan = & $context.Validate
                if ($null -eq $plan) { return }
                $context.Plan = $plan; $context.Running = $true
                & $context.Render 5
                try {
                    $context.Handle = & $context.OnStart $plan
                    if ($null -eq $context.Handle) { throw 'Installationsprogrammet returnerede ingen statusreference.' }
                    $context.Timer.Start()
                } catch {
                    $failure = $_.Exception.Message
                    & $context.SetGuidance 'Sådan får du hjælp til starten' 'Kopiér hjælp og log til skolens IT. Fortæl, om Windows viste en godkendelsesdialog, og om den blev godkendt eller afbrudt. Bevar den udpakkede installationspakke. IT skal kontrollere, om en installation stadig kører, før I prøver igen.' "Fejl ved start: $failure"
                    & $context.Finish 'failed' "Installationen kunne ikke startes: $failure" ''
                }
            }
        }
    }.GetNewClosure())
    $view.Controls.BackButton.Add_Click({
        & $context.ClearErrors
        $values = & $context.Read
        $previous = $context.Step - 1
        if ($context.Step -eq 4 -and $values.component -eq 'laser-station') { $previous = 2 }
        & $context.Render $previous
    }.GetNewClosure())
    $view.Controls.CancelButton.Add_Click({ $context.Window.Close() }.GetNewClosure())
    foreach ($name in @('SingleComputer','SeparateComputers','ServerComponent','StationComponent')) {
        $view.Controls[$name].Add_Checked({ if ($context.Step -eq 1) { & $context.Render 1 -KeepFocus } }.GetNewClosure())
    }
    $view.Controls.BrowseConnection.Add_Click({
        $dialog = [Microsoft.Win32.OpenFileDialog]::new()
        $dialog.Title = 'Vælg laserstationens forbindelsesfil fra serveren'
        $dialog.Filter = 'JSON-forbindelsesfil (*.json)|*.json'
        $dialog.CheckFileExists = $true; $dialog.Multiselect = $false
        if ($dialog.ShowDialog($context.Window)) { $context.Controls.ConnectionFile.Text = $dialog.FileName; & $context.ClearErrors }
    }.GetNewClosure())
    $view.Controls.NetworkCandidate.Add_SelectionChanged({
        $item = $context.Controls.NetworkCandidate.SelectedItem
        if ($null -eq $item) { return }
        $context.Controls.RoleIp.Text = [string]$item.address
        $context.Controls.StudentSubnet.Text = [string]$item.subnet
        $context.Controls.AddressConfirmed.IsChecked = $false
        $dhcp = switch -Regex ([string]$item.dhcp) { '^(True|Enabled|Yes|Ja)$' { 'Ja'; break }; '^(False|Disabled|No|Nej)$' { 'Nej'; break }; default { 'Ukendt' } }
        $gateway = [string]$item.gateway
        if (-not $gateway) { $gateway = 'Ikke oplyst' }
        $context.Controls.NetworkHint.Text = "Fundet på $($item.alias). Automatisk adresse (DHCP): $dhcp. Gateway: $gateway. Forslaget ændrer ingen netværksindstillinger; få det bekræftet af IT."
    }.GetNewClosure())
    foreach ($name in @('RoleIp','StudentSubnet')) {
        $view.Controls[$name].Add_TextChanged({ $context.Controls.AddressConfirmed.IsChecked = $false }.GetNewClosure())
    }
    foreach ($network in $Networks) {
        $candidate = [pscustomobject]@{
            alias=[string](Get-SetupWizardValue $network 'alias' '')
            address=[string](Get-SetupWizardValue $network 'address' '')
            subnet=[string](Get-SetupWizardValue $network 'subnet' '')
            dhcp=[string](Get-SetupWizardValue $network 'dhcp' 'Ukendt')
            gateway=[string](Get-SetupWizardValue $network 'gateway' 'Ikke oplyst')
            display=('{0} — {1}/{2}' -f (Get-SetupWizardValue $network 'alias' 'Netværkskort'), (Get-SetupWizardValue $network 'address' ''), (Get-SetupWizardValue $network 'prefixLength' ''))
        }
        [void]$view.Controls.NetworkCandidate.Items.Add($candidate)
    }
    if ($view.Controls.NetworkCandidate.Items.Count -eq 1) { $view.Controls.NetworkCandidate.SelectedIndex = 0 }
    elseif ($view.Controls.NetworkCandidate.Items.Count -eq 0) {
        $view.Controls.NetworkHint.Text = 'Ingen egnet IPv4-adresse blev fundet. Tilslut værkstedsnettet, og få adressen og netværksområdet fra IT. Du kan skrive oplysningerne i felterne nedenfor.'
    }
    $view.Controls.CopyChecklist.Add_Click({
        try {
            $checklist = [string](& $context.OnChecklist (& $context.Read))
            & $context.OnCopy $checklist
            $context.Controls.ChecklistStatus.Text = 'Tjeklisten er kopieret. Indsæt den i en mail eller besked til skolens IT.'
        } catch { $context.Controls.ChecklistStatus.Text = "Tjeklisten kunne ikke kopieres: $($_.Exception.Message)" }
    }.GetNewClosure())
    $view.Controls.CopyLog.Add_Click({
        try {
            $log = $context.Controls.InstallLog.Text
            if (-not $log) { $log = $context.Controls.StatusMessage.Text }
            & $context.OnCopy $log
            $context.Controls.LogCopyStatus.Text = 'Loggen er kopieret.'
        } catch { $context.Controls.LogCopyStatus.Text = "Loggen kunne ikke kopieres: $($_.Exception.Message)" }
    }.GetNewClosure())
    $view.Controls.CopyHelp.Add_Click({
        try {
            & $context.OnCopy ([string](& $context.HelpText))
            $context.Controls.HelpCopyStatus.Text = 'Hjælp og tekniske oplysninger er kopieret. Indsæt dem i en besked til skolens IT.'
        } catch { $context.Controls.HelpCopyStatus.Text = "Hjælpen kunne ikke kopieres: $($_.Exception.Message)" }
    }.GetNewClosure())
    $view.Window.Add_Closing({
        param($sender, $eventArgs)
        if ($context.Running) {
            $eventArgs.Cancel = $true
            $context.Controls.StatusMessage.Text = 'Installationen kører; vent til afslutning. Du kan kopiere loggen imens.'
        }
    }.GetNewClosure())
    $view.Window.Add_Closed({ $context.Timer.Stop() }.GetNewClosure())
    & $context.Render 1
    return $context
}

function Show-SetupWizard {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$PackageRoot,
        [object[]]$Networks = @(),
        [Parameter(Mandatory=$true)][scriptblock]$OnValidate,
        [Parameter(Mandatory=$true)][scriptblock]$OnStart,
        [Parameter(Mandatory=$true)][scriptblock]$OnPoll,
        [Parameter(Mandatory=$true)][scriptblock]$OnChecklist,
        [scriptblock]$OnCopy = { param([string]$Text) [Windows.Clipboard]::SetText($Text) }
    )
    $context = Initialize-SetupWizardView @PSBoundParameters
    try { [void]$context.Window.ShowDialog() } finally { $context.Timer.Stop() }
}
