param(
    [Parameter(Mandatory=$true)][string]$Repository,
    [Parameter(Mandatory=$true)][string]$Case
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $Repository 'deployment\school-setup\setup-wizard-view.ps1')

function Assert-View([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$script:Views = [Collections.ArrayList]::new()
$script:Networks = @(@{alias='Ethernet';address='192.168.1.20';prefixLength=24;subnet='192.168.1.0/24';dhcp=$true;gateway='192.168.1.1'})
$script:GoodValidate = {
    param($values)
    @{
        valid=$true; errors=@(); parameters=$values; entry='fixture-only'
        connectionSummary=@{schoolName='Testskolen';serverUrl='https://192.168.1.20:8780';stationId='laser-fhexa1';machine='fhexa1'}
    }
}
$script:GoodPoll = { param($handle) @{state='succeeded';exitCode=0;message='Fixture afsluttet';log='Fixture log';nextSteps='Kontrollér fixture-resultatet.'} }

function New-Fixture {
    param([scriptblock]$Validate=$script:GoodValidate, [scriptblock]$Poll=$script:GoodPoll, [object[]]$Networks=$script:Networks, [switch]$FailStart)
    $testState = @{ starts=0; plan=$null; copied=[Collections.ArrayList]::new(); failStart=[bool]$FailStart }
    $start = {param($plan) $testState.starts++; $testState.plan=$plan; if($testState.failStart){throw 'Fixture startfejl'}; @{fixture=$true}}.GetNewClosure()
    $copy = {param([string]$text) [void]$testState.copied.Add($text)}.GetNewClosure()
    $view = Initialize-SetupWizardView -PackageRoot $Repository -Networks $Networks -OnValidate $Validate -OnStart $start -OnPoll $Poll -OnChecklist {param($values) 'Fixture tjekliste'} -OnCopy $copy
    $view.TestState = $testState
    [void]$script:Views.Add($view)
    Assert-View (-not $view.Window.IsVisible) 'Fixture must never show a window.'
    return $view
}

function Click-Next($View) {
    $View.Controls.NextButton.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
}

function Go-ToStationReview($View) {
    $View.Controls.SeparateComputers.IsChecked=$true
    $View.Controls.StationComponent.IsChecked=$true
    $View.Controls.ConnectionFile.Text='C:\fixture-only\laser-station-connection.json'
    Click-Next $View
    Assert-View ($View.Step -eq 2) 'Station must visit the connection-file step.'
    Click-Next $View
}

function Go-ToServerReview($View) {
    Click-Next $View
    $View.Controls.SchoolName.Text='Testskolen'
    Click-Next $View
    $View.Controls.AddressConfirmed.IsChecked=$true
    Click-Next $View
    Assert-View ($View.Step -eq 4) 'Server must visit review before starting.'
    Assert-View ($View.TestState.starts -eq 0) 'Navigation must not start installation.'
}

function Wait-ForFixturePoll {
    $frame=[Windows.Threading.DispatcherFrame]::new()
    $timeout=[Windows.Threading.DispatcherTimer]::new()
    $timeout.Interval=[TimeSpan]::FromMilliseconds(950)
    $timeout.Add_Tick({$frame.Continue=$false}.GetNewClosure())
    $timeout.Start()
    try { [Windows.Threading.Dispatcher]::PushFrame($frame) } finally { $timeout.Stop() }
}

try {
    switch ($Case) {
        'load_view' {
            $view=New-Fixture
            foreach ($name in @('Step1','Step2','Step3','Step4','Step5','SchoolName','NetworkCandidate','RoleIp','StudentSubnet','ConnectionFile','ReviewSummary','InstallLog','NextButton','BackButton')) {
                Assert-View ($null -ne $view.Controls[$name]) "Missing control: $name"
            }
            Assert-View ($view.Window.MinWidth -eq 760 -and $view.Window.MinHeight -eq 600) 'Small windows must have declared minimum dimensions.'
            Assert-View ($view.Controls.PageScroll.VerticalScrollBarVisibility -eq 'Auto') 'Content must be scrollable at small sizes.'
            Assert-View ($view.Window.FontFamily.Source -match 'Metrophobic|Arial') 'The view must use the packaged font or fallback.'
        }
        'successful_flow' {
            $view=New-Fixture
            Go-ToServerReview $view
            Click-Next $view
            Assert-View ($view.Step -eq 5 -and $view.Running) 'Start must enter running state.'
            Assert-View ($view.TestState.starts -eq 1) 'Start callback must run exactly once.'
            Assert-View ($view.TestState.plan.parameters.schoolName -eq 'Testskolen') 'Reviewed values must reach the start callback.'
            Wait-ForFixturePoll
            Assert-View (-not $view.Running) 'Successful worker must end running state.'
            Assert-View ($view.Controls.StatusHeading.Text -eq 'Installationen er afsluttet') 'Successful exit must show completion.'
            Assert-View ($view.Controls.NextSteps.Text -eq 'Kontrollér fixture-resultatet.') 'Next steps must be visible.'
            Assert-View ($view.Controls.InstallLog.Text -eq 'Fixture log') 'Worker log must be visible.'
        }
        'step_scoped_validation' {
            $view=New-Fixture -Validate {param($values) @{valid=$false;errors=@(@{field='schoolName';message='Navnet mangler.'},@{field='roleIp';message='IP mangler.'})}}
            Click-Next $view
            Click-Next $view
            Assert-View ($view.Step -eq 2) 'Invalid school information must not advance.'
            Assert-View ($view.Controls.ErrorText.Text -eq 'Navnet mangler.') 'Show the school error, not a later network error.'
            Assert-View ($view.TestState.starts -eq 0) 'Validation failure must not start anything.'
        }
        'danish_dhcp' {
            foreach ($dhcpExample in @(@{input=$true;expected='Ja'},@{input=$false;expected='Nej'},@{input='unknown';expected='Ukendt'})) {
                $network=@{alias='Ethernet';address='192.168.1.20';prefixLength=24;subnet='192.168.1.0/24';dhcp=$dhcpExample.input;gateway='192.168.1.1'}
                $view=New-Fixture -Networks @($network)
                Assert-View ($view.Controls.NetworkHint.Text.Contains("DHCP): $($dhcpExample.expected).")) 'DHCP must have a Danish display value.'
                Assert-View ($view.Controls.RoleIp.Text -eq '192.168.1.20' -and $view.Controls.StudentSubnet.Text -eq '192.168.1.0/24') 'A single candidate must fill both fields.'
            }
        }
        'station_skips_network' {
            $view=New-Fixture
            Go-ToStationReview $view
            Assert-View ($view.Step -eq 4) 'Station must go directly from file selection to review.'
            Assert-View ($view.Controls.Step3.Visibility -eq 'Collapsed') 'Station must not ask for network settings.'
            Assert-View ($view.TestState.starts -eq 0) 'Station review must not start anything.'
        }
        'station_summary' {
            $view=New-Fixture
            Go-ToStationReview $view
            foreach ($value in @('Testskolen','https://192.168.1.20:8780','FLUX HEXA','laser-fhexa1')) {
                Assert-View ($view.Controls.ReviewSummary.Text.Contains($value)) "Station review must display $value"
            }
        }
        'station_back' {
            $view=New-Fixture
            Go-ToStationReview $view
            $view.Controls.BackButton.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            Assert-View ($view.Step -eq 2) 'Station back must return to file selection.'
            Assert-View ($view.Controls.ConnectionFile.Text -eq 'C:\fixture-only\laser-station-connection.json') 'Back must preserve the selected file.'
        }
        'address_confirmation_reset' {
            $view=New-Fixture
            $view.Controls.AddressConfirmed.IsChecked=$true
            $view.Controls.RoleIp.Text='192.168.1.21'
            Assert-View (-not $view.Controls.AddressConfirmed.IsChecked) 'Changing the address must invalidate the old confirmation.'
            $view.Controls.AddressConfirmed.IsChecked=$true
            $view.Controls.StudentSubnet.Text='192.168.1.0/25'
            Assert-View (-not $view.Controls.AddressConfirmed.IsChecked) 'Changing the subnet must invalidate the old confirmation.'
        }
        'nonzero_success_refused' {
            $view=New-Fixture -Poll {param($handle) @{state='succeeded';exitCode=7;log='Failure fixture'}}
            Go-ToServerReview $view
            Click-Next $view
            Wait-ForFixturePoll
            Assert-View (-not $view.Running) 'Finished worker must stop polling.'
            Assert-View ($view.Controls.StatusHeading.Text -eq 'Installationen blev ikke fuldført') 'Nonzero exit cannot be presented as success.'
            Assert-View ($view.Controls.StatusMessage.Text.Contains('kode 0')) 'Contradictory status must explain the missing success evidence.'
        }
        'poll_failure_keeps_guard' {
            $view=New-Fixture -Poll {throw 'Fixture polling exception'}
            Go-ToServerReview $view
            Click-Next $view
            Wait-ForFixturePoll
            Assert-View ($view.Running) 'Polling error does not prove the installer stopped.'
            Assert-View (-not $view.Controls.CancelButton.IsEnabled) 'Unknown running state must retain the close guard.'
            Assert-View ($view.Controls.StatusMessage.Text.Contains('kan stadig køre')) 'Unknown running state must be explained.'
        }
        'no_network_candidates' {
            $view=New-Fixture -Networks @()
            Assert-View ($view.Controls.NetworkCandidate.Items.Count -eq 0) 'An empty discovery result must remain empty.'
            Assert-View ($view.Controls.NetworkHint.Text.Contains('Ingen egnet IPv4-adresse')) 'Missing discovery must have a clear explanation.'
            Assert-View ($view.Controls.NetworkHint.Text.Contains('IT')) 'Missing discovery must point to IT help.'
            Assert-View ($view.Controls.RoleIp.Text -eq '' -and $view.Controls.StudentSubnet.Text -eq '') 'Do not fabricate network values.'
        }
        'closing_while_running' {
            $view=New-Fixture
            $view.Running=$true
            $view.Window.Close()
            Assert-View ($view.Controls.StatusMessage.Text.StartsWith('Installationen kører')) 'Closing a running installation must be cancelled with an explanation.'
            Assert-View ($view.Running) 'Closing must not silently cancel the worker.'
        }
        'embedded_context_help' {
            $view=New-Fixture
            Assert-View (-not $view.Controls.TopologyHelp.IsExpanded -and -not $view.Controls.NetworkHelp.IsExpanded) 'Context help must start folded.'
            Assert-View ($view.Controls.TopologyHelp.Header -eq 'Hvad skal jeg vælge?') 'Topology help must be discoverable in the flow.'
            Assert-View ($view.Controls.NetworkHelp.Header -eq 'Hvor finder jeg oplysningerne?') 'Network help must be discoverable in the flow.'
            $networkHelp = ($view.Controls.NetworkHelp.Content.Child.Children | ForEach-Object { $_.Text }) -join ' '
            foreach ($phrase in @('Flere netværkskort','Windows-VM','fysiske computer','ikke i sig selv en bekræftet reservation','Eksemplerne nedenfor viser kun formatet','tjeklisten')) {
                Assert-View ($networkHelp.Contains($phrase)) "Missing integrated explanation: $phrase"
            }
            Assert-View ($view.Controls.RoleIp.Text -ne '192.168.10.20') 'Format examples must not become default form values.'
        }
        'worker_guidance_and_technical_help' {
            $view=New-Fixture -Poll {param($handle) @{state='failed';exitCode=1;message='Fixture stoppet';log='Fixture log';guidanceTitle='Kontrollér forbindelsen';guidanceText='Bed IT kontrollere netværket.';technicalHelp="Fixture til IT`nGet-Service ElevhubFixture";nextSteps='Legacy text must not duplicate guidance.'}}
            Go-ToServerReview $view
            Click-Next $view
            Wait-ForFixturePoll
            Assert-View ($view.Controls.GuidanceHeading.Text -eq 'Kontrollér forbindelsen' -and $view.Controls.GuidanceText.Text -eq 'Bed IT kontrollere netværket.') 'Worker guidance must appear inside the installation view.'
            Assert-View ($view.Controls.TechnicalHelp.IsReadOnly -and $view.Controls.TechnicalHelp.AcceptsReturn) 'Technical help must be read-only multiline text.'
            Assert-View ($view.Controls.TechnicalHelpExpander.Visibility -eq 'Visible' -and -not $view.Controls.TechnicalHelpExpander.IsExpanded) 'Technical detail must be available but folded.'
            Assert-View ($view.Controls.NextStepsPanel.Visibility -eq 'Collapsed') 'New guidance must avoid duplicated legacy instructions.'
            $view.Controls.CopyHelp.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            Assert-View ($view.TestState.copied.Count -eq 1) 'Copy help must use the injected copy callback once.'
            foreach ($phrase in @('Fixture stoppet','Kontrollér forbindelsen','Bed IT kontrollere netværket.','Get-Service ElevhubFixture')) {
                Assert-View ($view.TestState.copied[0].Contains($phrase)) "Copied help must include $phrase"
            }
            Assert-View ($view.TestState.starts -eq 1) 'Copying technical commands must not invoke another process callback.'
        }
        'start_failure_has_own_help' {
            $view=New-Fixture -FailStart
            Go-ToServerReview $view
            Click-Next $view
            Assert-View (-not $view.Running -and $view.Controls.GuidancePanel.Visibility -eq 'Visible') 'Start error must end with a visible help box.'
            Assert-View ($view.Controls.GuidanceText.Text.Contains('godkendelsesdialog') -and $view.Controls.GuidanceText.Text.Contains('stadig kører')) 'Start error must explain approval and process checks.'
            Assert-View ($view.Controls.TechnicalHelp.Text.Contains('Fixture startfejl')) 'Start failure details must be preserved as text.'
            $view.Controls.CopyHelp.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            Assert-View ($view.TestState.copied[0].Contains('Fixture startfejl')) 'Start error help must be copyable to IT.'
        }
        'copy_log_and_checklist_are_inert' {
            $view=New-Fixture
            $view.Controls.CopyChecklist.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            Assert-View ($view.TestState.copied[0] -eq 'Fixture tjekliste') 'Checklist must use the injected clipboard callback.'
            $view.Controls.InstallLog.Text='Literal fixture log; Start-Process must remain text.'
            $view.Controls.CopyLog.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            Assert-View ($view.TestState.copied[1] -eq $view.Controls.InstallLog.Text) 'Log copy must preserve its literal text.'
            Assert-View ($view.Controls.CopyLog.Parent -is [Windows.Controls.Grid]) 'Copy log must share the log heading row above the field.'
            Assert-View ($view.TestState.starts -eq 0) 'Copying help must not start any installation.'
        }
        'legacy_callbacks_keep_guidance_fallback' {
            $view=New-Fixture
            Go-ToServerReview $view
            Click-Next $view
            Wait-ForFixturePoll
            Assert-View ($view.Controls.GuidanceHeading.Text -eq 'Kontrollér installationen') 'Old callbacks must still get contextual completion help.'
            Assert-View ($view.Controls.NextStepsPanel.Visibility -eq 'Visible') 'Old nextSteps must remain visible without new guidance fields.'
            Assert-View ($view.Controls.TechnicalHelpExpander.Visibility -eq 'Collapsed') 'Missing technical details must not create an empty expander.'
        }
        default { throw "Unknown fixture case: $Case" }
    }
    @{case=$Case;passed=$true;visibleWindows=$false;installationInvoked=$false} | ConvertTo-Json -Compress
} finally {
    foreach ($view in $script:Views) {
        $view.Running=$false
        $view.Timer.Stop()
        $view.Window.Close()
    }
}
