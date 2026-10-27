# Laserkø og laserstation

> Offentlig dokumentation: LOKAL_IP er en pladsholder. Brug skolens egne adresser.

Denne pakke klargør den eksisterende laserplan: elevaflevering, lærerkø,
forhåndsvisning, raster/vektor-valg, placering på den valgte laserplade,
materialevalg, manuel materialemarkering, lærergodkendelse, FLUX-tidsberegning,
Beam Studio-fil og tilbagemelding til køen.

Vælg skolens faktiske understøttede lasermodel, Beambox II (`fbb2`) eller
HEXA (`fhexa1`), og kontrollér den mod mærkat og producentsoftware.
[Den almindelige installationsguide](docs/TO-OPSAETNINGER.md) beskriver både
alt på én computer og central Windows-server med separat laserstation.
En VM er valgfri. Materialer markeres manuelt på fotoet. De tekniske kommandoer nedenfor må ikke
bruges til at geninstallere eller erstatte en eksisterende skolekø.

| Sted | Ansvar | Adresse efter installation |
|---|---|---|
| VM LOKAL_IP | Elevside, afleveringer, filer, lærerlogin og status | Elev: http://LOKAL_IP:8766/ |
| VM LOKAL_IP | Lærer-API, tilgængeligt fra hovedcomputerens IP | http://LOKAL_IP:8767/ |
| Makerspace-hovedcomputer | Laserstation, placering, materialer, FLUX og Beam Studio | http://127.0.0.1:8790/ |

VM'en behøver ikke Beam Studio eller forbindelse til laseren. Hovedcomputeren
henter listen og de valgte SVG-filer gennem lærer-API'et. Den opretter ikke en
ekstra afleveringskø. Genererede plader og en lokal oversigt gemmes i Dokumenter\Laserplader.
Stationen husker den seneste plade efter genstart, men gemmer ikke lærerkode
eller login-session på disk. Efter otte timer eller servergenstart logger man ind igen.

Åbn den færdige plade i Beam Studio, kontrollér placeringen og start dér.
Vælg først **Markér som skåret**, når arbejdet er udført. Det afslutter
afleveringen på serveren og rydder dens SVG-payload; historik og lokal plade
bevares. **Åbn i Beam Studio** markerer ikke automatisk opgaven færdig.
Løsningen er beregnet til én hovedcomputer ved laseren.

## Særskilt køinstallation på en ny Windows-vært

Pak ZIP-filen ud i en midlertidig mappe på VM'en. Find hovedcomputerens
aktuelle IPv4-adresse med `ipconfig` på hovedcomputeren. Brug ikke automatisk
administrationscomputerens adresse. Åbn PowerShell **som administrator** i
den udpakkede pakkemappe, og erstat `HOVEDCOMPUTERENS_IP` i kommandoen:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-laser.ps1 -Role Server -StationAddress HOVEDCOMPUTERENS_IP
```

Standardindstillinger:

- Program: `C:\Program Files\LaservaerkstedServer`.
- Kø/data: `C:\ProgramData\Laservaerksted\queue`.
- Automatisk opstart: Windows-opgaven `Laservaerksted-Queue`, som LocalService.
- Port 8766: elever fra `LOKAL_IP/24`.
- Port 8767: kun den angivne hovedcomputer.
- Lærerkoden oprettes i `C:\ProgramData\Laservaerksted\queue\laererkode.txt`.

Koden er laserens egen kode, ikke nødvendigvis Printværkstedets. Læs den
lokalt på VM'en som administrator, og indtast den i Laserstationen.
Eksisterende laserkø og kode bevares ved geninstallation med samme datamappe.
Pakken indeholder ingen elevdata eller lærer-/printerkoder.

Serveren genstartes automatisk efter procesfejl. Loggene ligger under
`queue\service`. Installationsscriptet opretter kun laserens egne opgave- og
firewallnavne; det ændrer ikke 3D-printserveren.

Adresserne bruger lokal HTTP i denne opsætning og må ikke portvideresendes
til internettet. En eksisterende HTTPS-proxy kan bruges med stationens
`-Server` og `-CAFile`; pakken opretter ikke certifikater eller lokal DNS.
Domænet print.example.invalid og port 80 er uændrede.

## Installér på hovedcomputeren

Beam Studio skal allerede være installeret. Pak samme ZIP ud og dobbeltklik
**INSTALLER-STATION.cmd**, eller brug:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-laser.ps1 -Role Station -Machine fhexa1 -Server http://LOKAL_IP:8767
```

Det kræver normalt ikke administratoradgang. Programmet installeres i
`%LOCALAPPDATA%\Laserstation`, og der oprettes en genvej **Laserstation** på
skrivebordet. Genvejen starter stationen skjult og åbner dens lokale side.
En allerede åben station genstartes ikke. Indtast laserens lærerkode,
vælg afleveringer, placér dem og vælg materiale.

Serveradressen og lokale mapper gemmes i `laser-installation.json`.
Materialer og huller tegnes og godkendes manuelt på fotoet.

Valgfrie argumenter til installationen er `-InstallPath`, `-PlatesPath`,
`-CAFile`, `-BeamStudioPath`, `-FluxApiPath`, `-Machine`, `-Port` og `-NoStart`. Programstierne
skal pege på eksisterende filer. En opdatering bevarer eksisterende
stationsindstillinger, medmindre de gives eksplicit. `-NoShortcut` bruges
til isolerede installationstests. Luk den eksisterende station før opdatering.
Beam Studio skal installeres separat; det er ikke med i pakken.

`-Machine fhexa1` vælger HEXA, mens `fbb2` vælger Beambox II. Uden et
eksplicit valg bevares en eksisterende maskinindstilling; nye installationer
bruger fortsat Beambox II som standard af hensyn til tidligere pakker.
HEXA bruger et konservativt placeringsområde på 730 × 410 mm og sine egne
20 FLUX-presets. Beam Studios HEXA-dokumentmål på 740 × 410 mm bruges i
BVG-metadata uden at skalere tegningskoordinaterne. Kameraområdet er kun
730 × 395 mm og må ikke strækkes til hele pladen.

## Materialefoto, områder og godkendelse

Vælg et foto, der viser hele det valgte arbejdsområde. Markér hjørnerne
øverst venstre, øverst højre, nederst højre, nederst venstre og bekræft
materialehøjden. Firepunktskorrektionen retter perspektivet; den erstatter
ikke fysisk kontrol af kameraets linseforvrængning og målestok.

Tegn materialets brugbare områder og huller manuelt på fotoet. Kontrollér
kanter, klemmer og målestok, og godkend områderne som lærer. Programmet
analyserer ikke fotoet automatisk, og fotoet sendes ikke til en analysetjeneste.

Automatisk placering bruger tegningernes afgrænsende rektangler med 3 mm
afstand fra materialekanter/huller og 4 mm mellem afleveringer. Den laver
ikke kontur-nesting. Flyt, drej eller fjern emner efter behov, og godkend den
samlede placering. Ændringer i foto, områder, tegning, placering, maskine
eller materialeindstillinger kræver en ny godkendelse.

**Klargør til laseren** beregner og gemmer en FCode-fil lokalt med FLUX i
simulation. **Send til laseren** kræver særskilt opsætning af den parrede
HEXA-forbindelse og er slået fra som standard. En sendekvittering bekræfter
kun filoverførsel, aldrig at laseren er startet. **Åbn i Beam Studio** er
fortsat tilgængelig. Fotoet bliver aldrig skæregeometri i den eksporterede fil.

Automatisk kamerahentning og samling af HEXA-kameraets delbilleder er endnu
ikke implementeret. Brug ikke et enkelt råt kamerabillede som foto af hele
pladen. Godkendelser og den aktive fotoscene gemmes ikke over genstart.

Hvis den eksisterende makerspace-klient viser designeditoren lokalt via
`app.laser_local`, skal dens afleveringsadresse sættes til
`http://LOKAL_IP:8766`. Stationens læreradresse er port **8767**.
Ret kun laserværkstedets indstilling, ikke klientens globale Elevhub-adresse.
Alternativt åbner elevens lasergenvej serverens `/laser.html` direkte.

## Lokal test uden VM eller skoleforbindelse

Pakken indeholder en test, som starter en midlertidig køserver og station
på frie localhost-porte. Den bruger udelukkende testdata og åbner ikke
Beam Studio eller en fysisk laser:

```powershell
.\runtime\python.exe .\scripts\verify_laser_offline.py --output .\testresultat --real-flux
```

`--real-flux` bruger den installerede Beam Studio-backend i simulation til
jobberegning. Udelad argumentet på en computer uden Beam Studio; så testes
kø, filoverførsel, pladegenerering og status, men ikke FLUX-beregningen.
Rapporten hedder `testresultat\verification.json`.

Testen kontrollerer aflevering og dubletbeskyttelse, lærerlogin,
elevadskillelse, genstart af køserver, hentning fra stationen, generering af
en lokal BVG-fil, genstart af stationen samt afslutning og oprydning på serveren.

På skolen mangler efterfølgende kontrol af de faktiske IP-adresser,
firewall/routing, makerspace-genvejen og fysisk kontrol i Beam Studio.
Se den daterede ændringsrapport for den faktisk gennemførte installation.

## Tilbageførsel

Server: stop/deaktivér kun opgaven `Laservaerksted-Queue`, og deaktivér
reglerne `Laservaerksted-Student` og `Laservaerksted-Station`, hvis løsningen
tages ud af drift. Bevar køens datamappe. Station: luk dens egen proces,
fjern genvejen, og bevar Dokumenter\Laserplader. En tidligere konfiguration
gemmes med dato ved geninstallation.

## Makerværkstedets laserworkflow

Makerværkstedets lærer-API kan kobles til den eksisterende laser-Inbox med
konfigurationen `makerspace_laser_queue`. Backend skal være den lokale
laser-lærertjeneste på `127.0.0.1:<teacher-port>`, og `key_file` peger på den
private nøgle, typisk `makerspace-laser.key`, relativt til Elevhubens
konfigurationsmappe. Nøglen må ikke vises i state, audit eller dokumentation.
Controllerens `station_id` skal være den samme lowercase slug som stationen i
Makerværkstedet; model skal være `fbb2` eller `fhexa1`.

Et eksempel på den relevante Elevhub-konfiguration er:

```json
{
  "makerspace_laser_queue": {
    "backend": "127.0.0.1:8767",
    "key_file": "makerspace-laser.key"
  }
}
```

`makerspace-laser.key` placeres i Inboxens datamappe med begrænsede
administratorrettigheder; den er ikke en del af en generisk kildeudgivelse.

En ny station oprettes deaktiveret. Læreren publicerer først et laserworkflow
og tildeler derefter en bestemt revision til stationen. Bindingen er
versionskontrolleret og fastholdes på et allerede bundet Inbox-job. Et job kan
bindes med `laser.job.bind`, når det stadig er `received` eller `ready`, og
stationen er aktiv med workflow. Et queue-job uden binding vælger ikke
automatisk en station.

Trinene `prepare`, `approve` og `produce` betyder lærerens tilladelse til lokal
klargøring og handoff. De starter ingen laser og sender ingen skjult
fjernstart. Handoff og forberedelsesclaim gemmes i `afleveringer.sqlite3` og
kan blokere andre plader, indtil jobbet annulleres og ryddes, eller fysiskafslutning efterfølges af `collect` og lærerens `clear`. Den fysiske status
er fortsat ukendt for overblikket. Ukendt uploadresultat genforsøges aldrig
automatisk; læreren skal kontrollere maskinen først.

Audit og receipts hører til den autoritative `afleveringer.sqlite3`. Den skal
backupes sammen med den private `makerspace-laser.key`; den eksisterende
`makerspace_backup.py` dækker endnu ikke disse laser-/kødata eller en samlet
gendannelse på tværs af tjenester.

Controller-bootstrap, nøgleudveksling, fysisk hardwaretest og afprøvning af
workflowets fulde handoff/clear-forløb mangler stadig på skolens udstyr. Der
er ingen krav om tilsluttede elevcomputere for denne administration. ISO- og
Windows-pakkerne er fortsat sat på hold, og ingen ny skoleinstallation
loves. **Kreditering: Klostermarksskolen.**
