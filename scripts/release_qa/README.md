# Udgivelsesprøver (release_qa)

Disse scripts bruges til at afprøve en udgivelseskandidat på testmaskiner,
før den når en skole. De er skrevet til at kunne genbruges. Alle navne,
stier og kontrolsummer angives som parametre.

Se også [udgivelsesprocessen](../../docs/UDGIVELSESPROCES.md).

## Hvad scripts aldrig gør

- De publicerer ikke noget.
- De rører ingen skolemaskiner og ingen skoledata.
- De starter ingen fysisk laser og sender ingen printjob.
- De gætter ikke legitimationer. Test-VM'en skal være udpeget af dig.

Nogle scripts installerer software på en test-VM eller i WSL. Kør dem kun
dér, hvor du har aftalt, at der må installeres.

## Legitimationsfiler

En legitimationsfil har to linjer: et brugernavn og et kodeord beskyttet
med DPAPI (`ConvertFrom-SecureString`, for den Windows-bruger der kører
scriptet). Filerne er privat testmateriale. De ligger uden for repoet og
må aldrig committes.

## Rækkefølge i en testkørsel

1. **Byg pakker og installere.** Elevpakker (deb) i WSL og Windows-installere
   med `windows/inno/build.py`. Se udgivelsesprocessen. Til en signeret
   kandidat-apt kan `build_signed_apt_candidate.py` bruges (se nedenfor).
2. **Installationssimulering.** Elevpakkerne simuleres i WSL, før de
   installeres rigtigt. Kvitteringen `install-simulation.json` bruges videre.
3. **WSL-installation af elevpakker.**
   `python3 install_student_packages.py <pakkemappe> <rapport.json> --expect NAVN=VERSION`
   (gentag `--expect` for hver pakke). Kræver en uindmeldt
   testmaskine med loopback-fixture. Scriptet afviser en indmeldt enhed.
   Det kontrollerer, at fixture-filer og elevprojekter er uændrede, at
   `dpkg --audit` er rent, og at værkstedsportene svarer.
4. **Browserprøver af de installerede værksteder.** Med Node fra mappen
   `produkt`, hvor `playwright` ligger i `node_modules`:
   `node scripts/release_qa/installed-laser.cjs <label> <outputmappe>`,
   `installed-folie.cjs <label> <outputmappe>` og
   `installed-3d.cjs <label> save <outputmappe>`. Genstart værkstedet og
   kør derefter `installed-3d.cjs <label> reopen <outputmappe>`.
5. **Laserstation på VM.**
   `Grant-StationBatchRight.ps1 -Mode Grant ...` giver testbrugeren retten
   til batch-logon.
6. **`Test-LaserStationOnVm.ps1`.** Installerer stationen som standardbruger
   og kontrollerer den. VM'en skal allerede køre og have en testserver.
7. **`Grant-StationBatchRight.ps1 -Mode Cleanup ...`** fjerner retten igen,
   hvis Grant tilføjede den. Brug samme `-ReportPath` som til Grant.
8. **Serveropdatering.** Tag først et checkpoint af VM'en. Kør derefter
   `Test-ServerUpdateOnVm.ps1`. Den opdaterer en eksisterende server og
   kontrollerer, at profil, nøgler og køer er bevaret.
   For en ren installation fra et checkpoint bruges
   `windows/inno/Test-InstallOnVm.ps1`.
9. **ISO-installation.** Brug en NY, tom Gen-2 VM med Secure Boot.
   Brug aldrig en VM med data. Styr installationen med `HyperVConsole.ps1`
   og `Wait-VmScreen.ps1` (se nedenfor). Vær opmærksom på:
   - Disk-spørgsmålet har som standard svaret "Nej". Det skal være "Ja".
   - Tastatur-spørgsmålet skal have Enter på "Dansk".
   - Installeren slukker selv VM'en, når den er færdig.
   - Fjern dvd-drevet, og start så fra disken.
10. **Signering med minisign.**
    `python sign_candidates.py --minisign <minisign.exe> --receipt <kvittering.json> --source-commit <40 tegn> <fil> [<fil> ...]`

## Scripts

| Script | Formål |
|---|---|
| `Test-LaserStationOnVm.ps1` | Station installeret som standardbruger på en VM med testserver. |
| `Grant-StationBatchRight.ps1` | Giver og fjerner batch-logon-retten for testbrugeren. |
| `Test-ServerUpdateOnVm.ps1` | Opdatering af en eksisterende server med kontrol af bevarede data. |
| `HyperVConsole.ps1` | Funktioner til skærmbillede og tastetryk på en VM. Indlæses med dot-source. |
| `Wait-VmScreen.ps1` | Venter på, at VM-skærmen står stille, og gemmer et skærmbillede. |
| `install_student_packages.py` | Installerer elevpakker i en WSL-testmaskine og kontrollerer resultatet. |
| `installed-laser.cjs`, `installed-folie.cjs`, `installed-3d.cjs` | Browserprøver af installerede værksteder. |
| `build_signed_apt_candidate.py` | Bygger et privat, signeret apt-lager til kandidaten. Publicerer og installerer ikke. |
| `sign_candidates.py` | Signerer kandidatfiler med minisign og skriver en kvittering. |

### ISO-installation med HyperVConsole og Wait-VmScreen

```powershell
. .\scripts\release_qa\HyperVConsole.ps1
Set-VmConsoleTarget -VmName '<ny test-VM>' -ShotDir '<mappe til skærmbilleder>'
Save-VmScreen 'trin-01.png'
Send-VmKey 13            # Enter
Send-VmText 'ja'
```

`Wait-VmScreen.ps1 -VMName <navn> -ScreenshotFolder <mappe> -Name trin-02.png`
venter, til skærmen er uændret (en prompt), eller til VM'en er slukket.

### Apt-kandidat

`python build_signed_apt_candidate.py --source <checkout> --upstream <byggemappe> --output <ny mappe>`

`--upstream` skal indeholde `report/` og `debian/` fra byggeren.
`--output` må ikke findes. Scriptet bruger den lokale udgivernøgle gennem
`scripts/local_release_signing.py` i `--source`.

### Byggescriptet build-current.sh

Det gamle `build-current.sh` er ikke med her. Det kaldte kun
`linux-elevhub/packaging/build_from_source.sh` med kildecheckout,
`byggeinput/linux`, `byggeinput/vaerktoejer`, Debian-inputmappen og en
arbejdsmappe, efter at det havde kontrolleret, at `HEAD` var det forventede
commit. Gør det samme i hånden, hvis du skal bygge igen.

## Resultater

Resultatfiler, rapporter, skærmbilleder og kvitteringer gemmes i
kandidatens leveringsmappe. De hører ikke hjemme i repoet. De kan
indeholde private stier og kontonavne.
