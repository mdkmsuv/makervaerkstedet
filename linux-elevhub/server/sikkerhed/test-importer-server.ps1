$ErrorActionPreference = 'Stop'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('elevhub-import-test-' + [guid]::NewGuid().ToString('N'))
$kilde = Join-Path $testRoot 'kilde'
$maal = Join-Path $testRoot 'maal'
New-Item -ItemType Directory -Path $kilde | Out-Null
try {
    foreach ($fil in @('ca.crt','server.crt','server.key','enheder.json','ca.key','elev-token.json')) {
        Set-Content -LiteralPath (Join-Path $kilde $fil) -Value ('KUN TEST ' + $fil)
    }
    & (Join-Path $PSScriptRoot 'importer-server.ps1') -Kilde $kilde -Maal $maal | Out-Null
    $filer = @(Get-ChildItem -LiteralPath $maal -File)
    if ($filer.Count -ne 4 -or (Test-Path -LiteralPath (Join-Path $maal 'ca.key'))) {
        throw 'Importen kopierede de forkerte filer.'
    }
    $acl = Get-Acl -LiteralPath $maal
    $tilladt = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18')
    if (-not $acl.AreAccessRulesProtected) { throw 'Mappen arver andre brugeres adgang.' }
    foreach ($fil in $filer) {
        $filAcl = Get-Acl -LiteralPath $fil.FullName
        foreach ($regel in $filAcl.Access) {
            if ($regel.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $tilladt) {
                throw 'En kopieret fil har uventede adgangsrettigheder.'
            }
        }
    }
    $afvist = $false
    try { & (Join-Path $PSScriptRoot 'importer-server.ps1') -Kilde $kilde -Maal $maal | Out-Null }
    catch { $afvist = $true }
    if (-not $afvist) { throw 'Eksisterende mål blev ikke afvist.' }
    Write-Output 'Windows-import: filvalg, private NTFS-rettigheder og afvisning af overskrivning bestod.'
} finally {
    # Kun testens egen tilfældige mappe må fjernes, aldrig en indtastet sti.
    $absolut = [IO.Path]::GetFullPath($testRoot)
    $tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $absolut.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($absolut) -notmatch '^elevhub-import-test-[0-9a-f]{32}$') {
        throw 'Testens oprydningssti kunne ikke valideres.'
    }
    Remove-Item -LiteralPath $absolut -Recurse -Force
}
