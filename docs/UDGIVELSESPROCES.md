# Sådan bygges og udgives en opdatering

Denne proces gælder ved forberedelse og gennemførelse af en faktisk
produktudgivelse eller udrulning. Den er ikke en obligatorisk opstartsrutine
for enhver kilde-, UI- eller dokumentrettelse.
Arbejdets organisering og mandat følger [AGENTS.md](../../AGENTS.md).
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
```
python -B windows/inno/build.py --inputs <inputs.json> --output <exe-mappe> --payload-output <payload-mappe>
windows/inno/Test-InstallOnVm.ps1 -VMName <test-VM> -Checkpoint <rent øjebliksbillede> -CredentialFile <privat fil> -Payloads <A.zip>,<B.zip>,<C.zip> -ResultFile <resultat.json>
```

Rækkefølgen af payloads skal være:
1. **A = den version, der kører på skolen nu.** Den installeres rent.
2. **B = den nye version.** Opdatering fra A til B er præcis det, skolen kommer til at opleve.
3. **C = den nye version bygget igen med næste versionsnummer.** Viser, at den nye version selv kan opdateres.

Består, når alle trin melder `ok`: rigtig version, begge opgaver kører, alle porte lytter, journalen står i `complete_paused`, og ingen programmapper er efterladt.
Payload for hver udgivet version gemmes i `bygninger/`, så A altid findes.

Testen kører samme kode som installeren, men klikker ikke gennem installerens sider. Ændres siderne (`Makervaerkstedet.iss`), skal EXE-filen også åbnes i hånden på test-VM'en.

### Port 6 — Elevpakker i WSL
Rører opgaven elevsystemet (alt, der ender i pakken `makervaerkstedet-elev` eller `makervaerkstedet-updater`), hæves
`CORE_VERSION` og `UPDATER_VERSION` i `linux-elevhub/packaging/prepare_inputs.py`, og pakkerne bygges i WSL-Debian som root:

```
bash linux-elevhub/packaging/build_from_source.sh <kilde> <byggeinput>/linux <byggeinput>/vaerktoejer <mappe med udgivne Scratch- og Matchbox-pakker> <ny arbejdsmappe>
```

Scriptet bygger kerne- og updater-pakken fra kilden, genbruger de udgivne Scratch- og Matchbox-pakker uændret og kører
`apt-get update` og en simuleret installation af alle fire mod Debian 13 med tom pakkestatus. Intet installeres på byggemaskinen.

Består, når `report/install-simulation.json` har `all_packages_simulated: true`, og forskellen i `*.inputs.json` mod forrige
udgivelse kun er de filer, opgaven har ændret.

Porten viser, at pakkerne kan installeres sammen. Den viser ikke, at en elevcomputer starter og virker bagefter.
Derfor prøves opdateringen på én elevcomputer, før resten får den.

### Port 7 — Udgivelse efter udtrykkelig bestilling
- Versionsnummer `2.0.0-ÅÅÅÅMMDD.N`. Et nummer genbruges aldrig, heller ikke til en fejlet bygning.
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
- **Installeren afprøves tidligt på mål-OS.** Ændringer i `windows/inno/` eller `windows-elevhub/server/install.ps1` får relevante installationsprøver under udvikling. Før en Windows-udgivelse skal hele port 5 være bestået, også når ændringen er lille.
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
Port 6 elevpakker:  <bygget / ikke berørt>
Ikke afprøvet:      <det, kvitteringen ikke dækker>
Signeret af:        <navn, dato>
Installeret:        <maskine, dato, kontrolleret af>
```
