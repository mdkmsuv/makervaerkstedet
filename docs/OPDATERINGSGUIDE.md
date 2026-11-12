# Opdateringsguide: fra ændring til skolens maskiner

Denne guide viser trin for trin, hvordan en ændring bliver til en opdatering: hvordan den laves, kontrolleres,
sendes til GitHub, bygges, udgives og kommer ud på serveren, laserstationen og elevcomputerne.

Skal du finde det rigtige sted at ændre, så start med [kodekortet](KODEKORT.md).
[Udgivelsesprocessen](UDGIVELSESPROCES.md) forklarer *hvorfor* reglerne findes og hvem der har ansvar for hvad.
Denne guide er *hvordan*: de konkrete kommandoer og hvad der skal stå på skærmen, når det er gået godt.

Alle kommandoer køres fra `produkt/`, medmindre andet står. `<…>` er noget, du selv udfylder.

---

## 1. Overblik: tre slags maskiner, tre veje

| Maskine | Hvad der installeres | Hvordan en opdatering kommer frem | Hvem starter den |
|---|---|---|---|
| Windows-server | `Makervaerkstedet-server-Setup.exe` | EXE-filen hentes og køres på serveren. Den finder selv den eksisterende installation. | Et menneske ved serveren |
| Laserstation (Windows) | Samme pakke som serveren | I dag med `Update-LaserStation.cmd` fra pakken. En særskilt laser-EXE er ikke bygget eller afprøvet. | Et menneske ved lasercomputeren |
| Elevcomputer (Debian) | Fire apt-pakker | Serveren henter den nyeste udgivelse fra GitHub, læreren vælger den i lærerpanelet, og elevcomputeren henter selv pakkerne fra serveren | Læreren i lærerpanelet |

To ting følger af tabellen:

- **Et push til GitHub opdaterer ingen maskiner.** Det gemmer kun kilden. Maskinerne får først noget, når der er bygget og udgivet pakker.
- **Windows opdateres aldrig automatisk.** Lærerpanelet kan ikke bestille en serveropdatering; den afvises med `windows_update_requires_role_installer`.

---

## 2. Hverdagen: lav en ændring og send den til GitHub

Dette er alt, der skal til for en almindelig kodeændring, som endnu ikke skal ud til skolen.

### 2.1 Start fra det nyeste

```
git checkout main
git pull
git checkout -b <kort-navn-paa-opgaven>
```

Arbejder flere sessioner i samme mappe, så commit kun dine egne filer (se 2.4), og rør ikke andres uafsluttede ændringer.

### 2.2 Lav ændringen

- Retter du en fejl, så skriv først en test, der fejler. Ret derefter fejlen, og se testen bestå.
- Skolens egne adresser, navne, koder og tilpasninger hører til i `skole-privat/` ved siden af `produkt/`, aldrig i kilden.
- Noget, der udgår, flyttes til `slet/` (mappen er ikke med i git) og slettes, når opgaven er godkendt.

### 2.3 Kontrollér før commit

Testene skal køres i testmiljøet, ikke i computerens almindelige Python. Det oprettes én gang, uden for OneDrive (i PowerShell):

```
python -m venv <mappe-uden-for-OneDrive>\venv
<venv>\Scripts\python -m pip install --no-index --find-links ..\byggeinput\windows\runtime-wheels (Get-ChildItem ..\byggeinput\windows\runtime-wheels\*.whl).FullName
```

Kør derefter de tests, ændringen berører, og før en udgivelse hele sættet:

```
<venv>\Scripts\python -B -m unittest discover -s tests -p "test_*.py" -t .
```

**Godt resultat:** de eneste tests, der fejler, står i `tests/KENDTE-FEJL.txt`. En test, der fejler og ikke står der, er en ny fejl og skal rettes.
Hele sættet tager omkring et kvarter.

Kontrollér derefter, at intet privat er kommet med:

```
python -B scripts/public_source.py --check
```

**Godt resultat:** én linje med `"files": <antal>` og ingen fejl. De almindelige afvisninger og hvad de betyder:

| Besked | Betyder | Gør dette |
|---|---|---|
| `Ukendt valgt kildefil … tilføj den eksplicit` | En ny fil står ikke på listen over filer, der må udgives | Føj stien til `explicitFiles` i `deployment/public-source-manifest.json`, hvis filen må være offentlig |
| `Privat person-/driftsoplysning: <fil>:<linje> [kategori]` | Linjen ligner en adresse, kode eller sti | Fjern oplysningen. Er det en syntetisk testværdi, registreres linjen i `privacyRules.reviewedExceptions` med en begrundelse |
| `Markdownlink går uden for eksporten` | Et link peger på en fil uden for `produkt/` | Skriv filnavnet som tekst i stedet for et link |
| `Fast skolenavn …` / `Lokalt skoletema …` | Skolens navn eller farver er skrevet fast ind i en side | Brug `data-school-name` og de neutrale farver |
| `Fonten mangler … pin` / `Billedet mangler … kontrolsum` | En ny skrifttype eller et nyt billede | Tilføj under `publicFonts` eller `publicImages` med hash og oprindelse |

### 2.4 Commit

```
git status --short
git add <dine filer, én for én>
git commit -m "<Hvad ændringen gør, og hvorfor>"
```

Brug ikke `git add -A`, når andre arbejder i mappen. Beskeden skal kunne læses om et år: hvad var galt, hvad gør ændringen, og hvordan er den afprøvet.

### 2.5 Send til GitHub

`main` på GitHub er offentlig. Kør derfor den strenge kontrol på det, der faktisk er committet, lige før du sender:

```
python -B scripts/public_source.py --check-repository
git push origin <din-branch>
```

Opret derefter en pull request på GitHub, og flet den ind i `main`, når den er set igennem.
Små rettelser kan sendes direkte: `git checkout main`, `git merge <din-branch>`, `git push origin main`.

**Send aldrig** de lokale branches `makervaerkstedet-2.0`, `v2/ubuntu-architecture` eller `install-ready-20261027`. De indeholder ældre historik med skolens adresser.

### 2.6 GitHubs automatiske kontrol

Ved hvert push kører workflowet "Kontrollér offentlig kildekode". De første to trin (eksport og private data) er dem, der beskytter mod at offentliggøre noget forkert.

Workflowet er endnu ikke tilpasset 2.0: flere af de senere trin kører tests af de gamle opdateringsscripts og fejler af den grund.
Indtil det er rettet, gælder: **trin 3 og 4 skal være grønne.** Et rødt kryds på et senere trin er kendt og blokerer ikke.

---

## 3. Hvornår skal der bygges en udgivelse?

| Ændringen rører | Byg og udgiv |
|---|---|
| Kun dokumenter, tests eller værktøjer | Ingenting. Push er nok. |
| `app/`, `web/`, `linux-elevhub/server/elevhub/`, `windows-elevhub/server/`, `deployment/school-setup/` | Windows-pakken (afsnit 4) |
| `windows/inno/` (installeren selv) | Windows-pakken, og VM-testen er ufravigelig |
| Noget, der ender på elevcomputeren: `linux-elevhub/projekter/`, `linux-elevhub/ansible/roles/kiosk/`, de filer fra `app/` og `web/`, som elevens tegneværksted bruger, `linux-elevhub/administration/` | Elevpakkerne (afsnit 5), ofte også Windows-pakken |
| `byggeinput/` (Python, BambuStudio, Scratch …) | En selvstændig opgave med nye fingeraftryk; derefter begge |

Er du i tvivl om, hvad en ændring rører, så byg begge og se i forskellen mod forrige udgivelse (4.3 og 5.2).

---

## 4. Windows: byg, test og udgiv

### 4.1 Vælg versionsnummer

`2.0.0-ÅÅÅÅMMDD.N`, hvor N tæller op samme dag. Et nummer genbruges aldrig, heller ikke til en bygning, der blev kasseret.
Til VM-testen skal du bruge tre numre: det, skolen kører nu (A), det nye (B) og det næste (C).

### 4.2 Byg fra ren kilde

Byg fra et rent udtræk af det commit, der skal udgives, ikke fra arbejdsmappen:

```
git worktree add <ren-mappe> <commit>
cd <ren-mappe>
npm ci --ignore-scripts
npm run build
npm run build:model3d
```

`npm run build` laver `web/vendor/viewer.js`, `laser.js` og `laser-trace-worker.js`. Uden dem virker 3D-visning og Laser/Folie ikke på serveren.

Byg derefter pakken, inputfortegnelsen og installeren. Outputmapperne skal være nye og ligge uden for OneDrive:

```
python -B scripts/build_school_package.py --output <pakke> --artifact-manifest <projektrod>\byggeinput\windows\artifact-manifest.json --role vm --theme green --version <version>
python -B windows/inno/prepare_inputs.py --role server --version <version> --package-root <pakke> --tools-root <projektrod>\byggeinput\vaerktoejer --public-review --output <inputs.json>
python -B windows/inno/build.py --inputs <inputs.json> --output <exe-mappe> --payload-output <payload-mappe> > <exe-mappe>\..\receipt.json
```

**Godt resultat:** `<exe-mappe>` indeholder `Makervaerkstedet-server-Setup.exe` (ca. 370 MB) og `build-provenance.json`.
`--public-review` er din bekræftelse af, at pakken ikke indeholder skoledata eller nøgler.

Pak payload-mappen til VM-testen (fra mappen, der indeholder `payload`):

```
tar -a -cf payload.zip payload
```

### 4.3 Sammenlign med forrige udgivelse

`build-provenance.json` har en fortegnelse (`input_files`) over hver fil i pakken med hash. Sammenlign den med forrige udgivelses.
Hver fil, der er kommet til, er forsvundet eller har ændret sig, skal kunne forklares af det, du har ændret.
En uforklaret forskel betyder, at bygningen er forkert; udgiv den ikke.

### 4.4 Test på test-VM'en

Testen installerer A rent, opdaterer til B og opdaterer til C på den lokale Hyper-V-maskine, som gendannes til sit rene øjebliksbillede først.
Intet uden for den maskine røres.

```
windows\inno\Test-InstallOnVm.ps1 -VMName <test-VM> -Checkpoint <rent øjebliksbillede> -CredentialFile <privat fil> -Payloads <A>\payload.zip,<B>\payload.zip,<C>\payload.zip -ResultFile <resultat.json>
```

Hver `payload.zip` skal have sin `receipt.json` liggende ved siden af. Regn med 30-40 minutter.

**Godt resultat:**

```
install  <A>   ~260 s  ok
update   <B>   ~450 s  ok
update   <C>   ~780 s  ok
```

Hvert trin kontrollerer: rigtig version, begge opgaver kører, portene 8770-8774 og 8780 lytter, journalen står i `complete_paused`,
køspærringen er ophævet, og ingen gamle programmapper er efterladt. Står der `FEJL`, er årsagen skrevet på samme linje; se afsnit 8.

Testen klikker ikke gennem installerens sider. Har du ændret `Makervaerkstedet.iss`, skal du også åbne EXE-filen i hånden på test-VM'en.

### 4.5 Gem det, der blev testet

Læg i `bygninger/windows-server-<version>/`: EXE, `build-provenance.json`, `receipt.json`, `payload.zip` og resultatfilen fra testen.
`payload.zip` er det, der gør næste udgivelses A → B-test mulig, så den må ikke mangle.

### 4.6 Signér (et menneske, på computeren med nøglen)

Udgivelser signeres med minisign. Den private nøgle ligger krypteret til din Windows-bruger i
`%USERPROFILE%\.makervaerkstedet\release-signing\` og må ikke kopieres til OneDrive eller til kilden.
`scripts/local_release_signing.py` har funktionen `sign_minisign(fil, minisign_exe=…)`, som signerer én fil, kontrollerer signaturen bagefter
og nægter at overskrive en eksisterende. Der er endnu ingen kommandolinje omkring den.

Den offentlige nøgle står i `deployment/keys/minisign.pub`. Kontrol af en signeret fil:

```
<projektrod>\byggeinput\vaerktoejer\minisign-windows\minisign-win64\x86_64\minisign.exe -V -m Makervaerkstedet-server-Setup.exe -P <nøglen fra minisign.pub>
```

**Nøglen har ingen sikkerhedskopi.** Går Windows-profilen tabt, kan der ikke udgives opdateringer, som de installerede maskiner accepterer.
Lav en beskyttet kopi, før den første rigtige udgivelse.

### 4.7 Udgiv på GitHub

1. Sørg for, at det commit, pakken er bygget af, ligger på `main` på GitHub.
2. Opret en udgivelse (Releases → Draft a new release) med et nyt tag på netop det commit.
3. Vedhæft `Makervaerkstedet-server-Setup.exe`, `Makervaerkstedet-server-Setup.exe.minisig` og `build-provenance.json`.
4. Skriv i beskrivelsen, hvad der er ændret, og hvad der er afprøvet (kopiér fra kvitteringen i afsnit 7).

Vigtigt, hvis udgivelsen også skal bære elevpakker: serveren læser kun **den seneste** udgivelse, som ikke er en kladde, og den forventer bestemte filer (afsnit 5.4).
En ren Windows-udgivelse som den nyeste gør derfor, at serveren ikke finder nogen elevopdatering, før de filer også ligger der.

### 4.8 Installér på skolens server (et menneske)

Følg `skole-privat/OPDATERING-AF-SKOLENS-SERVER.md`. Kort:

1. Øjebliksbillede af VM'en og backup.
2. `Foer-Opdatering.ps1` (standser skolens to egne indgange, som ellers blokerer opdateringen).
3. Kør EXE-filen som administrator. Serveren er nede i 7-13 minutter.
4. `Efter-Opdatering.ps1` (retter linket "Læreradgang" og starter indgangene igen).
5. Kontrollér lærerpanelet, printere og laser, og genoptag printkøen, som står på pause efter opdateringen.

Går noget galt: rør ikke ved filerne, gå tilbage til øjebliksbilledet, og gem `C:\ProgramData\ElevhubServer\installer-updates\<nyeste>`.

---

## 5. Elevcomputere: byg, test og udgiv

### 5.1 Hæv versionen og byg

Hæv `CORE_VERSION` og, hvis updateren er ændret, `UPDATER_VERSION` i `linux-elevhub/packaging/prepare_inputs.py`.
Uden et højere nummer installerer apt ikke den nye pakke.

Byg i WSL-Debian som root. Brug en arbejdsmappe under `/var/tmp`; `/tmp` tømmes, når WSL lukker.

```
wsl -d Debian -u root -- bash "<produkt>/linux-elevhub/packaging/build_from_source.sh" "<produkt>" "<projektrod>/byggeinput/linux" "<projektrod>/byggeinput/vaerktoejer" "<projektrod>/byggeinput/linux/debian-20261109.4" /var/tmp/<ny-mappe>
```

Stierne skal skrives som WSL ser dem (`/mnt/c/…`). Scriptet bygger kerne- og updater-pakken fra kilden, genbruger de udgivne
Scratch- og Matchbox-pakker uændret og kører `apt-get update` og en simuleret installation af alle fire mod Debian 13 med tom pakkestatus.
Intet installeres på byggemaskinen.

**Godt resultat:** `report/install-simulation.json` har `"all_packages_simulated": true`.

### 5.2 Sammenlign med forrige udgivelse

Hver pakke har en `*.inputs.json` med alle filer og deres hash. Sammenlign med den i `byggeinput/linux/debian-<forrige>/`.
Kun de filer, opgaven har ændret, må være forskellige.

### 5.3 Det, porten ikke viser

Den simulerede installation viser, at pakkerne passer sammen og kan installeres. Den viser ikke, at elevcomputeren starter,
at værkstederne åbner, eller at en computer med de gamle pakker kan opgradere. Det skal prøves på én elevcomputer (5.5).

### 5.4 Arkiv, feed og udgivelse

Serveren henter `https://api.github.com/repos/mdkmsuv/makervaerkstedet/releases/latest` og forventer præcis disse tre filer på udgivelsen:

| Fil | Indhold |
|---|---|
| `makervaerkstedet-apt-repository.zip` | Det signerede apt-arkiv med de fire pakker og et signeret manifest pr. komponent |
| `makervaerkstedet-updates.json` | Feedet: `schema`, `repository`, `release_tag`, `sequence` og zip-filens navn, hash og størrelse |
| `makervaerkstedet-updates.json.minisig` | Signaturen på feedet |

Tagget på udgivelsen skal være præcis det, der står i feedets `release_tag`. `sequence` er et heltal, der skal være højere end sidst;
serveren og elevcomputerne husker det højeste, de har set, og afviser lavere.

Filerne laves i to trin:

1. `scripts/build_apt_repository.py` (argumenter: `--inputs`, `--output`, `--gpg-exe`, `--gpg-sha256`, `--gnupg-home`, `--key-fingerprint`, `--receipt`)
   bygger og signerer arkivet med apt-nøglen. Input er en JSON-fil med de fire pakker og kvitteringen fra installationsporten. GPG-mappen må ikke ligge i OneDrive.
2. `scripts/build_simple_feed.py` har funktionen `build(repo, output, receipt, minisign_exe=…, minisign_sha256=…, public_key=…, release_tag=…, sequence=…)`,
   som laver zip-filen, feedet og signaturen.

**Mangler:** trin 2 og signeringen har ingen kommandolinje, og hele kæden fra færdige pakker til en udgivelse, som en server har hentet, er aldrig kørt.
Den første elevudgivelse skal derfor behandles som en afprøvning med én elevcomputer, ikke som rutine.

### 5.5 Udrul til elevcomputerne (læreren)

1. Serverens automatiske søgning er slået fra som standard (`simple_updates.source.enabled`). Den slås til i serverens konfiguration, eller læreren trykker "Søg efter opdateringer".
2. I lærerpanelet under Teknik vælges udgivelsen og de computere, der skal have den. Der bestilles pr. komponent (Elevsystem, Scratch, Vinduesstyring, Opdateringstjeneste).
3. Elevcomputeren spørger selv serveren hvert minut, henter pakkerne fra serveren, kontrollerer signaturen og installerer med apt. Slukkede computere får opdateringen, når de tændes.
4. Begynd med én computer. Kontrollér, at den starter, at værkstederne åbner, og at panelet viser den nye version, før resten bestilles.

---

## 6. Laserstationen

Laserstationen installeres fra samme pakke som serveren. Den er senest installeret med `Setup-LaserStation.ps1`, og opdateres med
`Update-LaserStation.cmd` fra en udpakket pakke. Installerens laserrolle (`--role laser_station`) er ikke bygget eller afprøvet i 2.0.
Opdatér derfor laserstationen sidst, og prøv bagefter hele vejen fra elevens aflevering til åbning i Beam Studio, uden at starte laseren.

---

## 7. Kvittering

Skriv én fil pr. udgivelse i `bygninger/<version>/KVITTERING.md` med skabelonen nederst i [udgivelsesprocessen](UDGIVELSESPROCES.md):
version, commit, hvad der blev testet og med hvilket resultat, hvad der *ikke* blev afprøvet, hvem der signerede, og hvor og hvornår det blev installeret.
Sig altid, hvilket niveau en påstand bygger på: læst, testet, bygget, installeret på test eller installeret på skolen.

---

## 8. Når noget fejler

| Du ser | Betyder | Gør dette |
|---|---|---|
| VM-test: `journal er 'pause_incomplete_no_stop'`, stderr `maintenance_owned_by_another_operation` | En tidligere opdatering har efterladt en spærring på køen | Fejl i installeren; den skal ophæve sin egen spærring. Se testen `test_two_updates_in_a_row_are_possible` |
| VM-test: `rollback_stop_failed_manual_recovery_required`, stderr `Task changed` | Installeren kunne ikke genkende sine egne opgaver efter at have slået dem fra | Fejl i `task_control.ps1`; se `test_own_disable_does_not_block_restore` |
| VM-test: `efterladte programmapper` | Oprydningen efter opdateringen blev afbrudt | Se `remove_tree` og `prune_finished_operations` i `lifecycle.py` |
| VM-test: `port … lytter ikke` eller `ElevhubServer er Ready` | Serveren startede ikke efter opdateringen | Læs `C:\ProgramData\ElevhubServer\logs\` og `hub\elevhub.log` på test-VM'en |
| Installer på skolen: `Owned runtime processes remain` | Noget kører stadig med programmets Python, typisk skolens egne indgange | Kør `Foer-Opdatering.ps1` først |
| Installer: `external_print_installation_not_supported` | Installationen har et separat, ældre printprogram | Den kan ikke opdateres af denne installer; ny installation kræves |
| Byg: `model3d_build_receipt_required` eller `model3d_source_hash_mismatch` | 3D-editorens byggede filer passer ikke til kilden | `npm run build:model3d` |
| Byg: `Artifact … matcher ikke SHA-256` | En fil i `byggeinput/` er ændret | Gendan fra `INDHOLD.json`, eller lav en opgave om nyt byggeinput |
| Elevpakker: `historical_app_inventory_changed:scratch` | Scratch-buildet passer ikke til fortegnelsen | Genbrug den udgivne Scratch-pakke (det gør `build_from_source.sh`) |
| Lærerpanelet: `windows_update_requires_role_installer` | Der er bestilt en Windows-opdatering fra panelet | Windows opdateres med EXE-filen, ikke fra panelet |

---

## 9. Det, der ikke er bygget eller afprøvet endnu

Listen er her, så ingen tror, at et trin er rutine, før det har været kørt.

- Opdatering af skolens server fra den version, den kører i dag (2.0.0-20261109.6). Testen er kørt fra en ren installation af den nye kode.
- Hele elevkæden: signeret apt-arkiv, feed på GitHub, serveren henter, læreren bestiller, elevcomputeren installerer.
- En kommandolinje til signering og til feedet (5.4), og en sikkerhedskopi af signeringsnøglen (4.6).
- Installerens laserrolle (afsnit 6).
- Installerens sider klikket igennem på en ren maskine.
- GitHubs workflows: `source-audit.yml` og `release.yml` bruger stadig de gamle opdateringsscripts (2.6).
- Én fælles lærerkode i praksis: print og laser accepterer panelkoden, men opretter og viser stadig deres egne koder.
