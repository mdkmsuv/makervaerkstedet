# Sådan bygges og udgives en opdatering

Denne proces gælder ved forberedelse og gennemførelse af en faktisk
produktudgivelse eller udrulning. Den er ikke en obligatorisk opstartsrutine
for enhver kilde-, UI- eller dokumentrettelse.
Arbejdets organisering og mandat følger `AGENTS.md` i projektroden (uden for denne kilde).
Formålet er fortsat, at en opdatering bygges fra reproducerbar kilde og
afprøves som installation og opdatering, før den når skolens udstyr.

## Hvorfor processen findes

Hver regel herunder svarer til en fejl, der faktisk er sket i projektet.

| Det skete | Følge | Reglen, der forhindrer det |
|---|---|---|
| Pakker blev lavet ved at kopiere enkeltfiler oven på en gammel pakke | Den installerede server manglede tre JavaScript-filer | Pakker bygges kun fra ren kilde (port 4) |
| Opdateringsvejen blev aldrig kørt fra ende til anden | Første opdatering efterlod serveren stoppet, fordi opgaverne ikke kunne startes igen | Installation og to opdateringer køres på en test-VM (port 5) |
| Fejl blev rettet direkte på den installerede server | Kilden og serveren var ikke længere ens | Ingen ændringer på installerede maskiner; alt går gennem en udgivelse |
| En uges arbejde lå uden commit | Ingen kunne se, hvad en pakke var bygget af | Bind udgivelsen til et dokumenteret commit; push/publicering kun efter konkret bestilling |
| Tests "fejlede" på grund af manglende pakker | Rigtige fejl druknede i 100 falske | Tests køres i det faste testmiljø (port 2) |
| En agent godkendte ud fra kodelæsning alene | "GO" betød ikke, at noget virkede | Bevisniveauer; en agent godkender aldrig sit eget arbejde |
| Skolens adresser lå i kilden til et offentligt repo | Risiko for at offentliggøre lokale oplysninger | Offentlighedskontrollen skal bestå før push (port 2) |
| Fem opdateringssystemer side om side | En rettelse ét sted manglede fire andre steder | Én opdateringsvej; gammelt fjernes, ikke parkeres |

## Roller

Ved en faktisk udgivelse skal disse ansvar dækkes. De er ikke fire faste
agentroller: hovedagenten kan udvikle, bygge og teste. Relevant uafhængigt
review varetages af en af højst tre underagenter eller en anden reviewer.

| Rolle | Ansvar | Må ikke |
|---|---|---|
| **Udvikler** | Ændrer kilden i en branch og skriver testen, der viser fejlen eller den nye funktion | Godkende sin egen ændring |
| **Reviewer** | Læser hele ændringen og leder efter fejl; skriver fundene ned | Rette koden selv; være den samme som udvikleren |
| **Bygger og tester** | Kører portene nedenfor på præcis det commit, der skal udgives, og skriver kvitteringen | Ændre kilden under en kørsel |
| **Udgivelsesansvarlig** | Brugeren beslutter publicering og drift; en agent kan udføre den konkrete bestilling med den godkendte signerings-/installationsvej | Må ikke udlede en driftstilladelse af en almindelig udviklingsopgave |

Agenter må bruge konkret aftalt læseadgang. Installation på en aftalt
testmaskine er tilladt inden for bestillingen. Ændringer på skolens drift
kræver udtrykkelig bestilling; en allerede givet tilladelse til den samme
handling skal ikke indhentes igen ved hvert trin.

## Bevisniveauer

Enhver statusmelding skal sige, hvilket niveau den bygger på. Et højere niveau kan ikke udledes af et lavere.

1. **Læst**: koden er gennemlæst.
2. **Testet**: de automatiske tests er kørt i testmiljøet, og resultatet er sammenlignet med listen over kendte fejl.
3. **Bygget**: pakken er bygget fra ren kilde og sammenlignet med forrige udgivelse.
4. **Installeret på test**: ren installation og opdatering er gennemført på test-VM'en (Windows) eller i WSL (elevpakker).
5. **Installeret på skolen**: gennemført på den rigtige maskine og kontrolleret bagefter.

En udgivelse kræver niveau 4. Ordet "klar" må kun bruges om niveau 4 eller 5.

## Forløbet

### Port 0 — Opgaven
Beskriv udgivelsens ændring, berørte komponenter og accept. Genbrug den
eksisterende opgavebeskrivelse; skriv ikke en ekstra plan alene for denne port.
Ændringer af databaser, konfiguration eller installer skal fremgå.

### Port 1 — Ændringen
- Ny branch fra `main`. Én opgave pr. branch.
- Hver rettet fejl får en test, der fejler uden rettelsen. Vis, at den fejler på den gamle kode.
- Skoleoplysninger, nøgler og lokale tilpasninger hører til i `skole-privat/` uden for kilden.
- Det, der udgår, flyttes til `slet/` og slettes, når opgaven er godkendt. Der efterlades ikke parallelle versioner.

### Port 2 — Kildekontrol
Køres i testmiljøet (Python med pakkerne fra `byggeinput/windows/runtime-wheels`), ikke i computerens almindelige Python.

```
python -B -m unittest discover -s tests -p "test_*.py" -t .
python -B scripts/public_source.py --check-repository
```

Består, når: ingen test fejler, som ikke står i `tests/KENDTE-FEJL.txt`, og offentlighedskontrollen melder 0 fund.
En ny fejl rettes; den føjes ikke til listen. Listen må kun blive kortere.

### Port 3 — Review
Revieweren læser hele forskellen mod `main` og svarer skriftligt på tre spørgsmål:
Kan dette ødelægge en eksisterende installation? Kan en opdatering standse halvvejs? Ændres data eller konfiguration, så en ældre version ikke kan læse dem?
Fund rettes af udvikleren og ses igen af revieweren.

### Port 4 — Byg fra ren kilde
Byg fra et rent udtræk af det commit, der skal udgives (`git worktree add <mappe> <commit>`), aldrig fra arbejdsmappen.
Fremgangsmåden står i `byggeinput/LAESMIG.md`. Outputmapper skal være nye.

Sammenlign derefter pakken med forrige udgivelses fortegnelse (`build-provenance.json`).
Hver tilføjet, fjernet eller ændret fil skal kunne forklares af opgaven. Kan den ikke det, er bygningen forkert.

Byggeinput ændres kun som en selvstændig opgave med nyt fingeraftryk i `artifact-manifest.json`.

### Port 5 — Windows på test-VM

Server-EXE'en bruger den understøttede `school-flat-v1`-installation og
opdateringsvej. Byg dens skolepakke med `scripts/build_school_package.py`
**uden `--generation-layout`**. En generationspakke er til den særskilte
vedligeholdelsesoperator og kan ikke installeres af denne EXE. Byggeren
afviser `requiresGenerationLayout: true` før kompilering; ændr ikke
flaget i en færdig pakke, men byg pakken igen med de korrekte valg.

Begge EXE-roller skal indeholde laserstationens komplette runtime- og
webaktiver; server-EXE'en tilbyder også alt-i-én-rollen. Byggeren kontrollerer
denne kontrakt før kompilering. Kør også den afgrænsede native portkontrol
på Windows, så en skrivefri preflight ikke opretter en netværkslistener:

```
powershell.exe -NoProfile -File tests/test_windows_install_port_probe.ps1
```

Den faktiske EXE-prøve skal skelne udpakning fra native succes: en kontrolleret
native fejl skal vises som **Installationen blev ikke færdig**, have den rigtige
log og give exitkode `20`. Først et bestået native trin må vise klar-siden.
Prøv både separat server/laserstation og Windows alt på én computer, herunder
afslutningens genveje, lærerlogin og opstart efter genstart.

```
python -B system/windows/inno/build.py --inputs <inputs.json> --output <exe-mappe> --payload-output <payload-mappe>
system/windows/inno/Test-InstallOnVm.ps1 -VMName <test-VM> -Checkpoint <rent øjebliksbillede> -CredentialFile <privat fil> -Payloads <A.zip>,<B.zip>,<C.zip> -ResultFile <resultat.json>
```

Rækkefølgen af payloads skal være:
1. **A = den version, der kører på skolen nu.** Den installeres rent.
2. **B = den nye version.** Opdatering fra A til B er præcis det, skolen kommer til at opleve.
3. **C = den nye version bygget igen med næste versionsnummer.** Viser, at den nye version selv kan opdateres.

Består, når alle trin melder `ok`: rigtig version, begge opgaver kører, alle porte lytter, journalen står i `complete_paused`, og ingen programmapper er efterladt.
Payload for hver udgivet version gemmes i `bygninger/`, så A altid findes.

Testen kører samme kode som installeren, men klikker ikke gennem installerens sider. Ændres siderne (`Makervaerkstedet.iss`), skal EXE-filen også åbnes i hånden på test-VM'en.

Scripts til de øvrige testkørsler (laserstation, serveropdatering, WSL-elevpakker, browserprøver, ISO-installation og signering) ligger i `scripts/release_qa/`; rækkefølgen står i [README](../scripts/release_qa/README.md).

### Port 6 — Modulære elevpakker og native prøve

Den fysiske kilde ligger i `system/`, `apps/<id>/` og `sdk/`.
Installerede URL'er og datastier ændres ikke med kildeopdelingen.

En appændring hæver kun appens version i `apps/<id>/app.json`;
hovedsystemet skal ikke genbygges eller have nyt versionsnummer.
Kerne og opdaterer versioneres hver for sig i
`system/linux-elevhub/packaging/prepare_inputs.py`, når deres indhold
ændres. Apppakker skal forblive tilvalg uden permanente kerneafhængigheder.

På Debian 13 amd64/WSL bruges den høje bygger fra committet kilde eller
verificeret offentlig eksport:

```bash
bash system/linux-elevhub/packaging/build_from_source.sh \
  /sti/til/kilde /sti/til/inputs/linux /sti/til/vaerktoejer \
  /sti/til/tidligere-pakker /sti/til/ny-arbejdsmappe --release-set base
```

Vælg `base` for præcis elevkerne, opdaterer og Matchbox,
`app --app <id>` for basis plus én app eller `migration` for basis
plus de fem indbyggede apps. Det sidste er udgivelsens samlede inputs,
ikke en permanent installation af alle apps.
Se [packaging-guiden](../system/linux-elevhub/packaging/README.md) og
[ny-app-guiden](NY-APP-GUIDE.md) for selvstændigt appbyg.

Kontrollér `release-build.json`: præcist sæt, hashes,
`all_packages_simulated: true` og `actual_install: false`.
Gaten bruger reel apt-opdatering og simulation med tom pakkestatus;
den installerer intet på byggemaskinen. Schema-1-kvitteringen med
de historiske fire pakker bevares kun til den gamle udgivelsesvej.

For en ny modulær udgivelse skal faktisk native installation på et
ejet, isoleret testmål desuden dokumentere:

- tom basis uden valgfrie appfliser samt fungerende start/projekthylde,
- appinstallering, opdatering, fjernelse og geninstallation med rigtig
  pakkeversion, descriptor, activation og rapporteret status,
- bevarelse af profiler, identitet, CA, nøgler, projekter, udbakker og
  relevante kø-/historikdata,
- kold offline opstart og genforbindelse med lærerens aktuelle adgang,
- ISO-installation og boot som særskilt prøve, når ISO udgives.

Migration fra den gamle samlede kerne prøves særskilt: observeret
bro-opdaterer først, derefter verificeret kerne og bevarede apps i én
afgrænset apt-transaktion. Laser, Folie, 3D og micro:bit bevares;
Scratch kun hvis det faktisk var installeret. Ingen ny projektnulstilling
eller erstatning med skolens standardvalg. Afbrydelse genoptager samme
bundne katalog/appbestand og kontrollerer den, også hvis kerneversionen
allerede matcher. Senere autoriseret afinstallation skal forblive fjernet.

Bygget pakke, apt-simulering, faktisk native installation, grafisk
kioskprøve, ISO-boot og fysisk hardwareunderstøttelse er forskellige
bevisniveauer. Først én aftalt elevenhed, derefter resten efter det
konkrete udrulningsmandat. Brug ingen fysisk køhandling som test.

### Port 7 — Udgivelse efter udtrykkelig bestilling
- Hver komponent beholder sit dokumenterede versionsformat: kerne/updater
  følger Debian-versionerne fra deres bygger; apps følger descriptoren;
  Windows-EXE følger sin releaseversion. Et versionsnummer med andre bytes
  genbruges aldrig. Katalogets sekvens stiger, og release-tag bindes præcist.
- Signér EXE og manifest med minisign. Nøglen ligger ikke i kilden eller i OneDrive.
- Opret udgivelsen på GitHub med EXE, signatur og `build-provenance.json`. Tag det commit, pakken er bygget af.
- Gem kvitteringen (se nederst) i `udgivelser/<version>/`.

### Port 8 — Udrulning efter udtrykkelig bestilling
1. Tag backup af serveren (`Manage-School`, punkt 9) og et øjebliksbillede af VM'en.
2. Vælg et tidspunkt uden undervisning. Serveren er nede under opdateringen; på test-VM'en tager den omkring ti minutter.
3. Kør installeren. Kontrollér bagefter: lærerpanelet åbner, versionen er den nye, printere og laser vises, og printkøen står på pause som forventet. Genoptag køen.
4. Én elevcomputer først, derefter resten.
5. Går noget galt: stop, rør ikke ved filerne på serveren, gå tilbage til øjebliksbilledet, og giv fejlen og journalen (`installer-updates/<id>/state.json`) videre som en ny opgave.

### Port 9 — Efterkontrol
Skriv i kvitteringen, hvad der blev installeret hvor og hvornår, og hvad der eventuelt afveg. Slet `slet/`.

## Faste regler

- **Kilden er sandheden.** Det, der kører på en maskine, skal kunne bygges fra et commit i `main` plus `byggeinput/`. Intet rettes i hånden på en installeret maskine.
- **Data overlever programmet.** En ændring af database eller konfiguration skal kunne læses af den forrige version, eller opgaven skal sige udtrykkeligt, at tilbagerulning ikke er mulig, og hvorfor.
- **Hver databaseændring får et nyt versionsnummer** i databasen, og programmet afviser en database, der er nyere end det kender.
- **Installeren afprøves tidligt på mål-OS.** Ændringer i `system/windows/inno/` eller `system/windows-elevhub/server/install.ps1` får relevante installationsprøver under udvikling. Før en Windows-udgivelse skal hele port 5 være bestået, også når ændringen er lille.
- **Fejlmeddelelser til brugeren er på dansk** og siger, hvad man skal gøre nu.
- **Ingen hemmeligheder i kilde, pakke eller chat.** Testkoder til test-VM'en ligger i en privat fil uden for kilden.

## Hvornår en agent skal stoppe og spørge

- Opgaven kræver en handling på skolens drift, som ikke er dækket af den
  udtrykkelige bestilling. Gentag ikke spørgsmålet for allerede aftalte trin.
- Der mangler en nødvendig beslutning eller tilladelse om data, kompatibilitet,
  byggeinput, signeringsnøgler, offentlighed eller mistet mulighed for rollback.
  Genbrug en beslutning, som allerede er givet for den konkrete opgave.
- En fejl blokerer efter begrundet undersøgelse uden en afklaret vej videre.
  Meld evidens og ukendt forhold; ret almindelige fejl selv inden for mandatet.
- En foreslået rettelse vil skjule fejl ved at svække kontrol eller flytte en
  ny fejl til listen over kendte fejl. En forældet test kan tilpasses til et
  udtrykkeligt ændret krav; dokumentér forbindelsen i stedet for at spørge igen.

## Kvittering

Én fil pr. udgivelse, `udgivelser/<version>/KVITTERING.md`:

```
Version:            2.0.0-ÅÅÅÅMMDD.N
Commit:             <hash på main>
Opgave:             <hvad og hvorfor>
Udvikler / reviewer / tester: <hvem>
Port 2 tests:       <kørt / fejlet / nye fejl: 0>
Port 2 offentlig:   <filer / fund: 0>
Port 3 review:      <fund og hvad der blev gjort>
Port 4 bygning:     <EXE sha256>, forskel mod <forrige version>: <tilføjet/fjernet/ændret>
Port 5 test-VM:     <A → B → C, tider, resultatfil>
Port 6 elevpakker:  <release-set, pakkehashes, simulation, native installation>
Migration/ISO:      <faktisk prøve eller udtrykkeligt ikke prøvet>
Ikke afprøvet:      <det, kvitteringen ikke dækker>
Signeret af:        <navn, dato>
Installeret:        <maskine, dato, kontrolleret af>
```
