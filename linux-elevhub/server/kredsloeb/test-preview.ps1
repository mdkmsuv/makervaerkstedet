[CmdletBinding()]
param([int]$Port = 8812)
$ErrorActionPreference = 'Stop'
$base = "http://127.0.0.1:$Port"
$checks = @('/', '/style.css', '/assets/metrophobic-latin.ttf', '/examples.js?v=20260920-7', '/app.js?v=20260920-5', '/vendor/circuitjs1/circuitjs.html', '/vendor/circuitjs1/circuitjs1/circuitjs1.nocache.js', '/vendor/circuitjs1/circuitjs1/setuplist.txt', '/vendor/circuitjs1/circuitjs1/locale_da.txt', '/vendor/circuitjs1/circuitjs1/gwt/clean/images/corner.png', '/vendor/circuitjs1/circuitjs1/gwt/clean/images/hborder.png', '/vendor/circuitjs1/circuitjs1/gwt/clean/images/vborder.png', '/vendor/circuitjs1/circuitjs1/circuits/resistors.txt', '/vendor/circuitjs1/iframe.html')
foreach ($path in $checks) {
  $response = Invoke-WebRequest -Uri ($base + $path) -UseBasicParsing -TimeoutSec 5
  if ($response.StatusCode -ne 200) { throw "$path svarede med HTTP $($response.StatusCode)." }
  Write-Output "OK $path ($($response.Content.Length) tegn)"
}
$html = (Invoke-WebRequest -Uri ($base + '/') -UseBasicParsing).Content
foreach ($needle in @('Få lys i din idé.', 'CircuitJS1')) {
  if ($html -notmatch [regex]::Escape($needle)) { throw "Forsiden mangler '$needle'." }
}
$examples = (Invoke-WebRequest -Uri ($base + '/examples.js') -UseBasicParsing).Content
foreach ($needle in @('Ohms lov', 'Seriekobling', 'Parallelkobling', 'LED med beskyttelse', 'Kondensatoren', 'Kontakt og lampe', '0 0 0 5 0 0', '162 272 128 272 256', '181 272 128 272 256', 's 208 128 272 128 0 1 false')) {
  if ($examples -notmatch [regex]::Escape($needle)) { throw "Eksempeldata mangler '$needle'." }
}
Write-Output "Alle Kredsløb-preview HTTP-kontroller bestået."
