# Forbered filerne til en USB-installation på RIGTIG hardware (ikke Hyper-V).
#
#   powershell -ExecutionPolicy Bypass -File "D:\Bambuslicer made easy\linux-elevhub\testmiljoe\vaert\lav-usb-stik.ps1"
#   powershell -ExecutionPolicy Bypass -File "...\lav-usb-stik.ps1" -Navn vaerksted-usbtest-01
#
# Udfylder testmiljoe\vm\preseed-usb.skabelon.cfg med computernavn og den
# OFFENTLIGE driftsnøgle, og pakker den som "elevhub.gz" (samme cpio/gzip-format
# som Hyper-V-installationsdisken bruger). Gemmer også den udfyldte preseed.cfg
# til byg-usb-iso.sh. Standardmappen er D:\Elevhub-VM\usb\.
#
# Dette script SKRIVER IKKE til nogen USB-nøgle eller fysisk disk. Det laver
# kun inputfiler til ISO-bygningen. Se USB-TEST-KLARGOERING.md for
# den fulde fremgangsmåde (én færdig ISO, Rufus, automatisk opstart).

param(
    [string]$Navn = 'vaerksted-usbtest-01',
    [string]$UdMappe = 'D:\Elevhub-VM\usb'
)

$ErrorActionPreference = 'Stop'

$Rod       = 'D:\Elevhub-VM'
$Skabelon  = Join-Path $PSScriptRoot '..\vm\preseed-usb.skabelon.cfg'
$NoegleSti = Join-Path $env:USERPROFILE '.ssh\elevhub_ed25519.pub'
$GzSti     = Join-Path $UdMappe 'elevhub.gz'
$PreseedSti = Join-Path $UdMappe 'preseed.cfg'
$IsoSti    = Join-Path $Rod 'iso\debian-13.7.0-amd64-netinst.iso'

if ($Navn -notmatch '^[a-z0-9][a-z0-9-]{0,62}$') {
    Write-Host "Ugyldigt computernavn: $Navn (kun små bogstaver, tal og bindestreg)" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $NoegleSti)) {
    Write-Host "Den offentlige driftsnøgle mangler: $NoegleSti" -ForegroundColor Red
    Write-Host "Den bruges allerede af testmiljøet (4-lav-installationsdisk.ps1); noget er ændret siden." -ForegroundColor Red
    exit 1
}
$noegle = (Get-Content $NoegleSti -Raw).Trim()
if ($noegle -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+ [\w.-]+$') {
    Write-Host 'Den offentlige nøgle har et uventet format.' -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $IsoSti)) {
    Write-Host "Debians netinst-ISO mangler: $IsoSti" -ForegroundColor Yellow
    Write-Host "Den er nødvendig som kilde ved bygning af den automatiske ISO. Se USB-TEST-KLARGOERING.md." -ForegroundColor Yellow
}

# 1. Udfyld skabelonen (Linux-linjeskift, UTF-8 uden BOM)
$preseed = [IO.File]::ReadAllText((Resolve-Path $Skabelon)) -replace "`r`n", "`n"
$preseed = $preseed.Replace('@@NAVN@@', $Navn).Replace('@@NOEGLE@@', $noegle)
if ($preseed -match '@@\w+@@') {
    Write-Host "Skabelonen har en pladsholder, der ikke blev udfyldt: $($Matches[0])" -ForegroundColor Red
    exit 1
}
$preseedBytes = (New-Object Text.UTF8Encoding $false).GetBytes($preseed)

# 2. Pak preseed.cfg i et cpio-arkiv (newc-format), komprimeret med gzip.
#    Kan bruges som ekstra initrd. Den automatiske USB-ISO bygges i stedet
#    med preseed.cfg indlejret via byg-usb-iso.sh.
function Skriv-CpioPost([IO.Stream]$ud, [string]$navn, [byte[]]$data, [int]$tilstand, [int]$inode) {
    $navnBytes = [Text.Encoding]::ASCII.GetBytes($navn + [char]0)
    $felter = @($inode, $tilstand, 0, 0, 1, 0, $data.Length, 0, 0, 0, 0, $navnBytes.Length, 0)
    $hoved = '070701' + (($felter | ForEach-Object { '{0:X8}' -f $_ }) -join '')
    $hovedBytes = [Text.Encoding]::ASCII.GetBytes($hoved)
    $ud.Write($hovedBytes, 0, $hovedBytes.Length)
    $ud.Write($navnBytes, 0, $navnBytes.Length)
    $fyld = (4 - (($hovedBytes.Length + $navnBytes.Length) % 4)) % 4
    $ud.Write((New-Object byte[] $fyld), 0, $fyld)
    $ud.Write($data, 0, $data.Length)
    $fyld = (4 - ($data.Length % 4)) % 4
    $ud.Write((New-Object byte[] $fyld), 0, $fyld)
}

New-Item -ItemType Directory -Force -Path $UdMappe | Out-Null
[IO.File]::WriteAllBytes($PreseedSti, $preseedBytes)
$fil = [IO.File]::Create($GzSti)
try {
    $gz = New-Object IO.Compression.GZipStream($fil, [IO.Compression.CompressionMode]::Compress)
    Skriv-CpioPost $gz 'preseed.cfg' $preseedBytes 0x81A4 1        # almindelig fil, 0644
    Skriv-CpioPost $gz 'TRAILER!!!' (New-Object byte[] 0) 0 0
    $gz.Close()
} finally {
    $fil.Close()
}

Write-Host "elevhub.gz er lavet: $GzSti" -ForegroundColor Green
Write-Host "Svarfil til ISO-bygningen: $PreseedSti" -ForegroundColor Green
Write-Host "  Computernavn: $Navn (kun DHCP-netværk; ingen fast testnet-adresse)"
Write-Host "  SSH: starter automatisk efter installationen; elevhub med den eksisterende driftsnøgle."
Write-Host ""
Write-Host "Næste trin (se USB-TEST-KLARGOERING.md for det fulde forløb):" -ForegroundColor Yellow
Write-Host "  1. Byg en ny automatisk ISO med byg-usb-iso.sh og den udfyldte preseed.cfg."
Write-Host "     Eksisterende ISO-filer er ikke ændret af dette script."
Write-Host "  2. Skriv den NYE ISO til én USB-nøgle med Rufus (DD-tilstand)."
Write-Host "  3. Start elevcomputeren fra USB-nøglen; installationen kører automatisk."
Write-Host "  4. Når maskinen har slukket, fjern USB-nøglen og tænd igen. SSH er klar på netværket."
