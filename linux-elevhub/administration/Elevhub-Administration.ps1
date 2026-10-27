[CmdletBinding()]
param([switch]$Test)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-LauncherRoot { Split-Path -Parent $PSScriptRoot }

function ConvertTo-WslPath([string]$WindowsPath) {
    if ([string]::IsNullOrWhiteSpace($WindowsPath)) { throw 'Stien er tom.' }
    $result = & wsl.exe -d Debian -- wslpath -a -u -- $WindowsPath 2>&1
    if ($LASTEXITCODE -ne 0) { throw "WSL kunne ikke konvertere stien: $WindowsPath`n$result" }
    $path = ($result | Select-Object -Last 1).ToString().Trim()
    if ([string]::IsNullOrWhiteSpace($path) -or -not $path.StartsWith('/')) { throw "Ugyldig WSL-sti fra wslpath: $path" }
    return $path
}

function New-KoerArguments {
    param(
        [ValidateSet('tjek','koer')][string]$Handling,
        [ValidateSet('CaDoodle','Hele elevsystemet')][string]$Scope,
        [Parameter(Mandatory)][string]$InventoryWsl,
        [string]$Limit
    )
    $args = [System.Collections.Generic.List[string]]::new()
    $args.Add('-d'); $args.Add('Debian'); $args.Add('--'); $args.Add('bash')
    $args.Add((ConvertTo-WslPath (Join-Path (Get-LauncherRoot) 'ansible/koer.sh')))
    $args.Add($Handling)
    $args.Add('-i'); $args.Add($InventoryWsl)
    if ($Scope -eq 'CaDoodle') { $args.Add('--tags'); $args.Add('cadoodle') }
    if (-not [string]::IsNullOrWhiteSpace($Limit)) {
        if ($Limit -notmatch '^[A-Za-z0-9_.:-]+$') { throw 'Værtsnavnet må kun indeholde bogstaver, tal, punktum, bindestreg, kolon eller underscore.' }
        $args.Add('--limit'); $args.Add($Limit.Trim())
    }
    return ,$args.ToArray()
}

function Start-KoerInTerminal([string[]]$Arguments) {
    $runner = Join-Path $PSScriptRoot 'Run-WslInTerminal.ps1'
    $argumentFile = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllText($argumentFile, (ConvertTo-Json -InputObject $Arguments), (New-Object Text.UTF8Encoding $false))
    # Only two Windows filenames cross the process boundary; command arguments
    # travel as JSON and never become shell source text.
    $child = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        ('"' + $runner + '"'), '-ArgumentFile', ('"' + $argumentFile + '"'))
    try { Start-Process -FilePath 'powershell.exe' -ArgumentList $child | Out-Null }
    catch { Remove-Item -LiteralPath $argumentFile; throw }
}

function Assert-LauncherTests {
    $inventory = '/mnt/d/Bambuslicer made easy/linux-elevhub/ansible/inventory/testmiljoe/hosts.yml'
    $old = (Get-Item Function:\ConvertTo-WslPath).ScriptBlock
    Set-Item Function:\ConvertTo-WslPath -Value { param([string]$WindowsPath) return '/mnt/d/example path/linux-elevhub/ansible/koer.sh' }
    try {
        $a = New-KoerArguments -Handling tjek -Scope CaDoodle -InventoryWsl $inventory -Limit 'vaerksted-01'
        $expected = @('-d','Debian','--','bash','/mnt/d/example path/linux-elevhub/ansible/koer.sh','tjek','-i',$inventory,'--tags','cadoodle','--limit','vaerksted-01')
        if ((ConvertTo-Json -InputObject $a -Compress) -cne (ConvertTo-Json -InputObject $expected -Compress)) { throw 'Forkert rækkefølge eller værdi i argumenterne.' }
        $b = New-KoerArguments -Handling koer -Scope 'Hele elevsystemet' -InventoryWsl $inventory
        if ($b -contains '--tags' -or $b -contains 'cadoodle') { throw 'Hele elevsystemet må ikke få cadoodle-tagget.' }
        try { New-KoerArguments -Handling koer -Scope CaDoodle -InventoryWsl $inventory -Limit 'bad name' | Out-Null; throw 'Ugyldigt limit blev accepteret.' } catch { if ($_.Exception.Message -notmatch 'Værtsnavnet') { throw } }
        $roundtrip = @('/mnt/d/path with spaces/hosts.yml', 'a trailing backslash\', 'quote"and$literal')
        $argumentFile = [IO.Path]::GetTempFileName()
        try {
            [IO.File]::WriteAllText($argumentFile, (ConvertTo-Json -InputObject $roundtrip), (New-Object Text.UTF8Encoding $false))
            $received = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Run-WslInTerminal.ps1') -ArgumentFile $argumentFile -Test
            [string[]]$receivedArguments = ConvertFrom-Json -InputObject ($received -join "`n")
            if ($LASTEXITCODE -ne 0 -or (ConvertTo-Json -InputObject $receivedArguments -Compress) -cne (ConvertTo-Json -InputObject $roundtrip -Compress)) { throw 'Argumenter blev ændret på vej til den nye proces.' }
        } finally { if (Test-Path -LiteralPath $argumentFile) { Remove-Item -LiteralPath $argumentFile } }
        'OK: argumentbygning bestået (WSL og netværk blev ikke startet).'
    } finally { Set-Item Function:\ConvertTo-WslPath -Value $old }
}

if ($Test) { Assert-LauncherTests; exit 0 }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$root = Get-LauncherRoot
$defaultInventory = Join-Path $root 'ansible/inventory/skole/fra-serveren.py'

$form = [Windows.Forms.Form]::new(); $form.Text = 'Elevhub administration'; $form.Size = [Drawing.Size]::new(650,430); $form.StartPosition = 'CenterScreen'; $form.MinimumSize = $form.Size
$font = [Drawing.Font]::new('Segoe UI', 10)
$form.Font = $font
$label = { param($text,$x,$y,$w=560); $l=[Windows.Forms.Label]::new();$l.Text=$text;$l.Location=[Drawing.Point]::new($x,$y);$l.Size=[Drawing.Size]::new($w,28);$form.Controls.Add($l);return $l }
&$label 'Computerliste (fra-serveren.py henter elevcomputerne fra serveren)' 22 20
$inventoryBox=[Windows.Forms.TextBox]::new();$inventoryBox.Location=[Drawing.Point]::new(22,48);$inventoryBox.Size=[Drawing.Size]::new(500,28);$inventoryBox.Text=$defaultInventory;$form.Controls.Add($inventoryBox)
$browse=[Windows.Forms.Button]::new();$browse.Text='Gennemse…';$browse.Location=[Drawing.Point]::new(530,47);$browse.Size=[Drawing.Size]::new(90,29);$form.Controls.Add($browse)
$browse.Add_Click({$d=[Windows.Forms.OpenFileDialog]::new();$d.Filter='Computerliste fra serveren|fra-serveren.py|Ansible inventory (hosts.yml)|hosts.yml|YAML-filer (*.yml;*.yaml)|*.yml;*.yaml';$d.Title='Vælg inventory';if($d.ShowDialog() -eq 'OK'){$inventoryBox.Text=$d.FileName}})
&$label 'Valgfri vært (fx vaerksted-01)' 22 92
$limitBox=[Windows.Forms.TextBox]::new();$limitBox.Location=[Drawing.Point]::new(22,120);$limitBox.Size=[Drawing.Size]::new(598,28);$form.Controls.Add($limitBox)
&$label 'Omfang' 22 164
$cadoodle=[Windows.Forms.RadioButton]::new();$cadoodle.Text='CaDoodle';$cadoodle.Location=[Drawing.Point]::new(22,192);$cadoodle.Size=[Drawing.Size]::new(290,28);$cadoodle.Checked=$true;$form.Controls.Add($cadoodle)
$hele=[Windows.Forms.RadioButton]::new();$hele.Text='Hele elevsystemet';$hele.Location=[Drawing.Point]::new(330,192);$hele.Size=[Drawing.Size]::new(220,28);$form.Controls.Add($hele)
$notice=[Windows.Forms.Label]::new();$notice.Text='Tjek viser planlagte ændringer. En elevskærm i brug genstartes ikke; ændringen virker fra næste opstart. CaDoodles programfil skiftes først, når programmet er lukket.';$notice.Location=[Drawing.Point]::new(22,232);$notice.Size=[Drawing.Size]::new(598,45);$notice.ForeColor=[Drawing.Color]::DarkRed;$form.Controls.Add($notice)
$check=[Windows.Forms.Button]::new();$check.Text='Tjek ændringer';$check.Location=[Drawing.Point]::new(22,300);$check.Size=[Drawing.Size]::new(180,42);$form.Controls.Add($check)
$apply=[Windows.Forms.Button]::new();$apply.Text='Anvend opdatering';$apply.Location=[Drawing.Point]::new(220,300);$apply.Size=[Drawing.Size]::new(180,42);$form.Controls.Add($apply)
$close=[Windows.Forms.Button]::new();$close.Text='Luk';$close.Location=[Drawing.Point]::new(440,300);$close.Size=[Drawing.Size]::new(180,42);$close.Add_Click({$form.Close()});$form.Controls.Add($close)
$run = { param([string]$handling); try { if(-not (Test-Path -LiteralPath $inventoryBox.Text -PathType Leaf) -or [IO.Path]::GetFileName($inventoryBox.Text) -notin @('fra-serveren.py','hosts.yml','hosts.yaml')){throw 'Vælg fra-serveren.py eller en eksisterende hosts.yml/hosts.yaml-fil.'};$iw=ConvertTo-WslPath $inventoryBox.Text;$scope=if($cadoodle.Checked){'CaDoodle'}else{'Hele elevsystemet'};if($handling -eq 'koer' -and $scope -eq 'Hele elevsystemet' -and [Windows.Forms.MessageBox]::Show('Hele elevsystemet kan ændre kiosk og tjenester. Elevskærme i brug genstartes ikke; ændringerne virker fra næste opstart. Vil du fortsætte?','Bekræft opdatering',[Windows.Forms.MessageBoxButtons]::YesNo,[Windows.Forms.MessageBoxIcon]::Warning) -ne 'Yes'){return};Start-KoerInTerminal (New-KoerArguments -Handling $handling -Scope $scope -InventoryWsl $iw -Limit $limitBox.Text)}catch{[Windows.Forms.MessageBox]::Show($_.Exception.Message,'Kunne ikke starte', 'OK','Error')}}
$check.Add_Click({&$run 'tjek'});$apply.Add_Click({&$run 'koer'})
[Windows.Forms.Application]::Run($form)
