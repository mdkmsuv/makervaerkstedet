# Backup af Makerværkstedets serverdata

> Offentlig dokumentation: LOKAL_IP er en pladsholder. Brug skolens egne adresser.

**Kreditering: Klostermarksskolen.** Backup og isoleret gendannelsesprøve er særskilte kontroller. Aktivering af en gendannet server er
et særskilt forløb; de beskrevne Windows-værktøjer til dette kræver
`generation-v1` og passer ikke til standardpakkens flade layout.
Se [de adskilte installations- og opdateringsveje](KOM-GODT-IGANG.md#vælg-først-ren-installation-eller-opdatering).

## Skolemenuen

Udviklingskoden i `Manage-School.cmd` har nu tre indgange:

- **9: Opret og kontrollér offline backup.** Læser installationens ejerskab og
  bruger dens hub-, print- og laserdata. Ved `generation-v1` fastholdes det
  aktive generationsvalg under backupen; data låses særskilt.
- **10: Kontrollér en eksisterende backup.** Kontrollerer manifest,
  kontrolsummer, filoversigt og databaser.
- **11: Gendan til en isoleret prøve.** Kræver en ny mappe og bevarer alle
  opstartsspærrer. Dette aktiverer ikke en ny server.

For standardlayoutet oprettes profilen i hukommelsen af
[`app/school_backup.py`](../app/school_backup.py). Ejerskab, installeret
tilstand og datarod skal stemme. Manglende køer eller eksterne backends
udelades ikke: menuen afviser sådanne installationer. Det avancerede
profilværktøj nedenfor kan beskrive flere datarødder.

Tjenesterne skal stadig være stoppet før punkt 9. De nye menupunkter stopper
eller genstarter dem ikke. Kontrol og isoleret gendannelse af en eksisterende
backup kan ske uden at skrive til driftsdata.

Hvis den oprindelige installation er tabt, kan en kontrolleret kopi af
udviklingsværktøjet kontrollere og gendanne backupen uden adgang til kilden:

```powershell
python -m app.school_backup verify --source 'D:\Beskyttet-backup\skole-01'
python -m app.school_backup restore --source 'D:\Beskyttet-backup\skole-01' --destination 'D:\Gendannelsesproever\skole-01'
```

Skolemenuens vellykkede resultat vises først efter fuldført kontrol.
En afbrudt operation kan efterlade en ufuldstændig mappe, som bevares til
eftersyn og ikke overskrives ved genforsøg. Brug en ny destination. Backup
og gendannelsesprøve er lokale, private kopier; der udføres ingen upload.

Dette afsnit beskriver den generiske, isolerede `app.school_backup`
gendannelse. Den udskifter ikke den aktive installation, fjerner ingen
holdmarkører og må ikke bruges til at starte en vilkårlig gendannet mappe.
Til en eksisterende `generation-v1`-installation findes der nu et særskilt,
journalført administratorforløb gennem den faste Windows-wrapper nedenfor.

## Windows-operator for opdatering, restore og rollback

**Kun `generation-v1`.** Dette er ikke standardpakkens opdateringsvej eller
en metode til at konvertere en eksisterende flad installation. Ren
installation, versionsopdatering og gendannelse fra backup skal vælges
og efterprøves hver for sig.

Den installerede faste wrapper
`C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd`
starter installationens runtime og understøtter `update`, `restore`,
`rollback`, `resume`, `cancel` og `status`. Forældremapperne
`C:\ProgramData\MakervaerkstedetVedligehold` og
`C:\ProgramData\MakervaerkstedetGenerationer` skal findes og være
adminbeskyttede. Destinationsmapperne selv skal ikke findes og må ikke
overlappe; `-CompletedFolder` ved rollback er den eksisterende afsluttede
operationsmappe og er den tilsigtede undtagelse.

```powershell
& 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd' -Action update `
  -RuntimeRoot 'C:\Program Files\ElevhubServer' `
  -Folder 'C:\ProgramData\MakervaerkstedetVedligehold\opdatering-01' `
  -MaterializedFolder 'C:\ProgramData\MakervaerkstedetGenerationer\generation-ny' `
  -ProgramRoot 'D:\Kandidater\program'

& 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd' -Action restore `
  -RuntimeRoot 'C:\Program Files\ElevhubServer' `
  -Folder 'C:\ProgramData\MakervaerkstedetVedligehold\gendannelse-01' `
  -MaterializedFolder 'C:\ProgramData\MakervaerkstedetGenerationer\generation-gendannet' `
  -ProgramRoot 'D:\Kandidater\program' `
  -BackupSource 'E:\Beskyttet-backup\makerspace-backup-01'

& 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd' -Action rollback `
  -RuntimeRoot 'C:\Program Files\ElevhubServer' `
  -Folder 'C:\ProgramData\MakervaerkstedetVedligehold\tilbagefoersel-01' `
  -MaterializedFolder 'C:\ProgramData\MakervaerkstedetGenerationer\generation-tilbage' `
  -CompletedFolder 'C:\ProgramData\MakervaerkstedetVedligehold\opdatering-01'

& 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd' -Action resume -RuntimeRoot 'C:\Program Files\ElevhubServer' -Folder 'C:\ProgramData\MakervaerkstedetVedligehold\opdatering-01'
& 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd' -Action cancel -RuntimeRoot 'C:\Program Files\ElevhubServer' -Folder 'C:\ProgramData\MakervaerkstedetVedligehold\opdatering-01'
& 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd' -Action status -RuntimeRoot 'C:\Program Files\ElevhubServer' -Folder 'C:\ProgramData\MakervaerkstedetVedligehold\opdatering-01'
```

`restore` accepterer kun en verificeret historisk backup fra samme
installation og skole. Ejerskab, netværkskontrakt, skoleprofil og CA-
identitet skal matche den nuværende host; forløbet reprovisionerer ikke en
mistet host eller ændrer skolens identitet. Den valgte backup bruges til den
nye generation, mens den aktive og tidligere generation bevares.

`rollback` bruger den tidligere programversion fra den afsluttede opdatering
sammen med en frisk kopi af de aktuelt valgte data. Den genindsætter ikke en
gammel databackup efter genåbnet drift. `resume`, `cancel` og `status` bruger
kun journalens gemte parametre. `status` returnerer blandt andet fase,
vedligeholdelsesspærre, materialiseret mappe, om tasks er genoprettet og
`queues_unpaused: false`; printkøen genåbnes ikke automatisk.

Operatoren gemmer oprindelig `enabled`/`running`-tilstand for
`ElevhubServer` og `ElevhubSchoolEnrollment`, kontrollerer task-ejerskab og
kommandoer, stopper begge kontrolleret, etablerer ACL på den nye generation
efter en fuld reparse-point-kontrol og gendanner kun den kvitterede tilstand.
Journalen bevarer fase og kvittering ved afbrydelse, så operationen kan
genoptages eller annulleres sikkert.

Generationsforløbet kræver særskilt Windows-afprøvning af installation,
rettigheder, opgavestart, restore og afinstallation. Wrapperen gendanner ikke
en mistet vært; erstatningsværten er en særskilt gendannelsesvej.

## Samlet, offline kopi af serverens datamapper

`app/installation_backup.py` samler de angivne datamapper, herunder hub,
print og laser, i én kontrolleret backup. Hele mappernes filindhold kopieres:
maskinopsætning, køer, workflowversioner, jobfiler, lokale kvitteringer,
lærerlogin, tilmeldinger, certifikater og private forbindelsesnøgler, når de
ligger i disse mapper. SQLite-databaser kopieres gennem SQLite-backup-API'et;
indhold i WAL-journaler samles i selvstændige databasefiler.

**Alle berørte tjenester skal være stoppet.** Værktøjet tager eksklusive
datalåse for hele operationen og afviser en kørende, understøttet tjeneste,
før backupmappen oprettes. Det stopper ingen tjenester selv. Runtimekoden
for server, hub, print, laser og lokal laserstation skriver
`backup-protocol.json`, når den har erhvervet sin levetidslås. Manglende eller
forkert protokol afvises. Gamle installationspakker uden denne protokol er
ikke understøttet; man må ikke fremstille en markør manuelt for at omgå
kontrollen. Tilbageførsel til ældre kode kræver sin egen kompatibilitetsprøve.

Skoleopsætningens tilmeldingstjeneste er også en selvstændig skriver.
Når en datarod har `school.json` eller `security/enrollment`, kræves dens
protokol i `security/enrollment/backup-protocol.json`. Backupen holder både
tjenestens levetidslås og dens separate administrationslås. Dermed kan
tilmeldingskoder udstedes og tilmeldinger tilbagekaldes under normal drift,
men ikke samtidig med backup. SSH-administrationens skrivning af kendte
værtsnøgler bruger samme administrationslås. En gammel skoleopsætning uden
protokollen afvises også, når hubtjenesten er stoppet.

Når en datarod indeholder `laserstation/laser-installation.json`, læses også
stationens eksplicitte `plates`-sti. Dens plademappe skal indgå i backupens
rødder og understøtte protokollen `laser_station`. Backupen tager stationens
levetidslås i selve plademappen (normalt `Laserplader/instance.lock`). En
kørende station eller plader uden for de angivne rødder afvises før kopiering.

Backupen tager desuden den eksisterende lås ved det konfigurerede
enhedsregister, eksempelvis `security/enheder.lock`, så registerværktøjets
skrivning også udelukkes. Denne bestemte låsefil og tjenesternes
`instance.lock` kopieres ikke; andre filer med endelsen `.lock` bevares.
Tilmeldingsjournalen `security/enrollment.json` og de registrerede
SSH-identiteter i `security/ssh-pins.json` følger med datakopien.
Tilmeldingstjenesten holder sin lås, indtil aktive forespørgsler er afsluttet.
En samlet netværksfrist afbryder også klienter, der sender meget langsomt.

Opret en privat JSON-profil med de **faktiske** datastier. Dette er et eksempel
for Windows-standardlayoutet, hvor alle tre tjenester ligger under samme rod:

```json
{
  "format": 1,
  "roots": {
    "server": {"path": "C:\\ProgramData\\ElevhubServer", "role": "server"}
  },
  "components": {
    "hub": {"root": "server", "path": "hub"},
    "print": {"root": "server", "path": "printvaerksted"},
    "laser": {"root": "server", "path": "designvaerksted"}
  },
  "config": {"root": "server", "path": "konfiguration.json"}
}
```

En ekstern printdatamappe på samme computer angives som en ekstra rod med
rollen `print`; komponenten henviser så til denne rods `path: "."`.
Tilsvarende gælder `hub`, `laser` og en stations datamappe med rollen
`laser_station`. Rødder må ikke overlappe. En ubrugt print- eller
laserkomponent angives som `null`; hub kræves. En konfigureret køforbindelse
skal have sin komponent og den korrekte private nøgle med. TLS-filer og
enhedsregister skal være indeholdt i rødderne, undtagen ved den eksisterende
udviklingsindstilling `usikker_test: true`.

Kør fra kildekodens rod med dens Python-miljø. Destinationens forældremappe
skal findes; selve destinationen skal være ny:

```powershell
python -m app.installation_backup snapshot --profile 'C:\Privat\backup-profil.json' --destination 'D:\Beskyttet-backup\makerspace-backup-01'
python -m app.installation_backup verify --source 'D:\Beskyttet-backup\makerspace-backup-01'
python -m app.installation_backup restore --source 'D:\Beskyttet-backup\makerspace-backup-01' --destination 'D:\Gendannelsesproever\makerspace-backup-01'
```

Kommandoerne giver JSON og exitkode 0 ved succes, 1 ved fejl. Kontrollen
sammenholder fil- og mappeoversigt, filstørrelser, SHA-256, databasernes
kendte skemaer og SQLite-integritet. Backup kan kontrolleres og gendannes,
selv om de oprindelige datamapper ikke længere findes. Ukendte
SQLite-skemaer afvises; databaser kopieres ikke ukontrolleret som almindelige
filer. Links, reparse points, UNC-stier, overlappende kilder og eksisterende
destinationer afvises. Grænsen er 200.000 filer/undermapper og 64 MiB manifest.

Hub kræver SQLite-version 1. Print og laser accepterer version 0 eller 1,
men kræver i begge tilfælde de kendte workflowtabeller og -kolonner. Kopien
bevarer versionsnummeret; backupen migrerer ikke kilden. En efterfølgende
[programkompatibilitetskontrol](MAKERSPACE-OPDATERING.md) kræver version 1.
Dette gør ikke vilkårlige ældre installationer kompatible med de nye værktøjer.

Windows-destinationen får adgang for den udførende bruger, SYSTEM og
Administrators; nedarvede tilladelser fjernes. På POSIX er mapper `0700` og
filer `0600`. Backupen indeholder skolens hemmeligheder og elevdata og skal
holdes uden for kildeeksport og installationspakker. Kontrolsummer opdager
ændringer, men er ikke en digital signatur eller kryptering. Beskyttet
transport, separat backupmedium, tidsplan og retention er stadig særskilte
driftsopgaver. Eksisterende kopier slettes aldrig automatisk.

## Gendannelse forbliver isoleret

Gendannelsen bruger en ny mappe og bevarer originalen. Databaser, jobs,
workflowbindinger, genforsøgskvitteringer og private kønøgler følger med.
Eventuelle historiske agentnøgler tilbagekaldes i kopien, og printkøen sættes på pause. Usikre
jobtilstande som `dispatching` og laserens allerede tildelte handoff bevares;
de nulstilles ikke til en ny, startklar opgave.

`.restore-hold.json` i kopiens rod og komponentmapper blokerer de ændrede
tjenesters opstart, herunder tilmeldingstjenesten og dens lokale
administrationskommandoer. TLS-, register- og kønøglereferencer i hubkonfigurationen
omskrives til kopien. Printeradresser og øvrige program-/eksterne filstier
bevares og skal vurderes før en senere ibrugtagning. Fjern ikke markørerne
for at prøve kopien mod værkstedets udstyr. En godkendt aktiveringsproces med
kontrol af fysisk køstatus er endnu ikke implementeret.

`restore-report.json` skrives først efter gennemført gendannelse. Ved fejl
bevares en eventuel delvis destinationsmappe til undersøgelse; der foretages
ingen automatisk oprydning eller genbrug af denne mappe. En afbrudt restore
har holdmarkør, men ingen successrapport. En backup skal have bestået
`verify`; tilstedeværelsen af et manifest alene er ikke et succesbevis.

Dette er en samlet **databackup for de erklærede lokale mapper**, ikke en
geninstallation af hele Windows-serveren. Programfiler, tjeneste-/opgaveregistrering,
firewallregler, ekstern billedserver, elevprojekter uden for rødderne og
lasercontrollerkonfiguration uden for stationsdatamappen indsamles ikke
automatisk. Den journalførte Windows-operator håndterer nu update, restore,
rollback, resume, cancel og status for en eksisterende generation-v1-host;
den faktiske administratorafprøvning på Windows er stadig udestående.
Den interne tilbageførselskerne bruger tidligere program med en frisk kopi af
aktuelle data; den genindfører ikke en gammel databackup efter genåbnet drift.
Se [opdatering](MAKERSPACE-OPDATERING.md).

Ved backup og kontrol af uændrede kildedata behandles kun en faktisk
SQLite-databases `-shm`-læseindeks og tomme `-wal` som flygtige sidefiler.
SQLite kan oprette dem ved læsning. WAL med indhold, rollbackjournal,
databasen selv og uvedkommende filer sammenlignes fortsat. En tom WAL, som
vokser under kopieringen, bliver derfor opdaget. Prøverne kontrollerer også
en committed databaseskrivning under backup og afviser den.

## Den tidligere kopi af kun hubdatabasen

`linux-elevhub/server/elevhub/makerspace_backup.py` tager en konsistent kopi
af Makerværkstedets database via SQLite online backup API. Databasen kan
kopieres, mens tjenesten skriver. Kilde og kopi kontrolleres for skemaversion,
nødvendige tabeller/kolonner og SQLite-integritet. Kontrollen garanterer ikke,
at alle forretningsdata er meningsfulde, eller at fysisk udstyr svarer til dem.

### Hvad er omfattet af den enkelte database?

Databasen ligger som `makerspace.sqlite3` i den mappe, lærerens HTTPS-tjeneste
er startet med gennem `--data`. Find den faktiske værdi i installationens
tjeneste-/opgavedefinition; antag ikke, at en bestemt standardmappe bruges.

Kopien indeholder manuelle stationer, workflowkladder og publicerede
revisioner, manuelle job med fastlåst workflowversion, request-ID'er til
genforsøg samt revisionslog. Eventuelle gamle agenttabeller fra tidligere
udviklingsudgaver bevares som historik. Nye databaser opretter dem ikke;
den aktuelle kode har ingen agentlogin eller nøgleudstedelse.

Printerændringers offentlige kvitteringer og HMAC-aftryk ligger også her.
De autoritative printeropsætninger, kvitteringer og den private
`makerspace-bridge.key` ligger i printertjenestens datamappe. Nøglen skal
bevares sammen med opsætningen: udskiftning af den bryder genkendelsen af
tidligere printerkommandoer. Hverken nøgle, driftsopsætning eller databaser må
komme med i en generisk kilde- eller installationspakke. Brug den samlede
databackup ovenfor, når disse komponenter skal kopieres sammen.

Printkøens autoritative jobstatus, workflowbindinger, receipts og auditdata
ligger i printerkøens `queue.sqlite3`, ikke i værktøjets
`makerspace.sqlite3`. `makerspace_backup.py` sikkerhedskopierer derfor ikke
disse data. `app.installation_backup` medtager dem, når printdatamappen er
erklæret i profilen; den aktiverer fortsat ikke en gendannelse i drift.

Dette er kun én databasekomponent. Printer-/laseropsætning og køer, herunder laserens `afleveringer.sqlite3`
med audit/receipt-metadata og den lokale `makerspace-laser.key`,
elevprojekter, tilmeldinger, lærerlogin, certifikater og øvrige konfigurations-
og datafiler kræver den samlede datakopi ovenfor eller egne konsistente
kopier. Elevprojektbackup omfatter ikke automatisk denne serverdatabase.
Planlægning, retention og driftsgendannelse mangler stadig.

### Opret en ny kopi af hubdatabasen

Kør med Python fra kildekodens rod. Følgende stier er eksempler, som skal
erstattes med installationens faktiske lokale mapper:

```powershell
python linux-elevhub/server/elevhub/makerspace_backup.py snapshot --source 'C:\Eksempel\Serverdata\makerspace.sqlite3' --destination 'D:\Eksempel\Beskyttet-backup\makerspace-backup-01.sqlite3'
```

Destinationen skal være en ny fil. Eksisterende destinationer og tilhørende
SQLite-sidefiler afvises. Direkte UNC-stier og symlinks som kildefil eller
destinationsfil afvises. Brug en lokal mappe med adgang begrænset til skolens
administratorer; Windows-filen arver mappens rettigheder. På POSIX oprettes
kopien med rettigheden `0600`. Kopier skal ikke indgå i en kildeeksport.

Værktøjet skriver et JSON-resultat og afslutter med kode 0 ved succes og 1
ved fejl. Resultatet oplyser blandt andet integritetsstatus og antal sider,
men udskriver ikke databaseindhold. Operationen har en tidsgrænse på 30
sekunder og kan afvises under vedvarende belastning. Ved fejl lukkes SQLite-
forbindelserne, og kun den nyoprettede destinationsfil forsøges fjernet.
Der slettes aldrig gamle kopier automatisk.

Kopier ikke en levende `makerspace.sqlite3` med almindelig filkopiering som
erstatning for dette trin. Når snapshot er afsluttet, kan det indgå i skolens
beskyttede backuptransport til et andet medium. Transport og kryptering
udføres ikke af dette værktøj.

### Afprøv gendannelse til en separat fil

```powershell
python linux-elevhub/server/elevhub/makerspace_backup.py restore --source 'D:\Eksempel\Beskyttet-backup\makerspace-backup-01.sqlite3' --destination 'D:\Eksempel\Isoleret-test\makerspace.sqlite3'
```

Gendannelsen opretter og kontrollerer en ny database. Den erstatter ikke den
aktive database og starter eller stopper ingen tjenester. Kildekopien
ændres ikke. Workflowversioner, jobhistorik og genforsøgsoplysninger bevares.

**Eventuelle historiske agentnøgler tilbagekaldes i den gendannede database.**
Den daglige agentstyring er fjernet, så hverken nye eller gamle agenttokens
giver adgang i den aktuelle kode. Tilbagekaldelsen beskytter historiske
kopier, hvis de senere åbnes med ældre programkode. Der skal ikke udstedes
nye agentnøgler. Almindelig `snapshot` bevarer kilden uændret; brug `restore`
til at forberede en gendannelse frem for at anvende snapshotfilen direkte.

Automatiserede tests bruger midlertidige databaser og dækker samtidig
skrivning, fastlåste jobversioner, genforsøg efter gendannelse, tilbagekaldelse
af nøgler, ugyldigt skema, tidsgrænse, fejl under kopiering og beskyttelse af
eksisterende filer. Det erstatter ikke en samlet gendannelsesprøve med
serverens øvrige data og hardware; aktiv udskiftning er endnu ikke en
implementeret arbejdsgang.

## Erstatningsvært efter tab af Windows-serveren

Der findes et særskilt, udtrykkeligt gendannelsesforløb til
en ny Windows-vært. Det bruger `app.school_recovery` og installationsvalget
`-RecoveryBackup`. En almindelig frisk installation opretter en ny ejer og
CA og kan derfor ikke bagefter overtage den gamle skolebackup. Brug
gendannelsesvalget fra begyndelsen.

Forløbet kræver en verificeret, samlet `generation-v1` skolebackup samt
**præcis den oprindelige installationspakke**, som backupens ejer har
bundet med kontrolsummer. Pakken skal indeholde den nye gendannelsesindgang.
De tidligere, uændrede installationssnapshots har ikke denne indgang;
de kan ikke omtales som færdige gendannelsespakker. Bevar den installerede
versions oprindelige pakke sammen med fremtidige backups.

Erstatningsværten skal bruge den samme IPv4-adresse, subnet, læreradresse,
porte, skoleprofil og rolle. Program- og datamapper skal være nye og
adskilte. Eksterne backends eller ændring af skolens netværk er ikke del af
dette forløb. Den gamle vært må ikke samtidig køre på samme adresse.

Fra pakkens rod kan administrator først kontrollere kilden uden at skrive:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\school-setup\Setup-School.ps1 `
  -GenerationLayout -RecoveryBackup 'D:\Beskyttet-backup\skole-backup' `
  -SchoolName 'Eksempelskolen' -Role vm -RoleIp 'LOKAL_IP' `
  -StudentSubnet 'LOKAL_IP/24' -Theme green -CheckOnly
```

Erstat eksempelværdierne med backupens faktiske profil, og angiv også
eventuelle ændrede backend-/lærerporte. For en makerspace-station skal
`-Role makerspace` og den oprindelige `-Machine` bruges. Udelad derefter
`-CheckOnly` for installationen i en administratorkonsol. Det er dette trin,
der genopretter Windows-rettigheder, certifikattillid, opgaver og firewall.
Efter en afbrydelse bruges den samme kommando og den samme backup/pakke igen.
Ingen mapper skal slettes eller ejerskabsfiler redigeres manuelt.

Klargøringen bevarer skoleejer, CA/TLS-nøgler, SSH-identitet, tilmeldinger,
workflows, elevfiler og kvitteringer. Private datastier flyttes til den nye
datarod. Kopien har startspærrer, og printkøen er pauset. En afbrudt kopi
bevares; genforsøg kopierer til en ny intern prøvemappe og publicerer først
færdige, kontrollerede data. Installeren journalfører frigivelse af spærrer
efter pakke-, identitets- og rettighedskontrol. Før første tjenestestart
journalføres et særskilt checkpoint, så eventuelle nye elevfiler efter en
mislykket opstart ikke overskrives eller afvises som ændrede backupbytes.
Fysisk køstatus skal afklares før læreren genåbner printkøen.

De isolerede tests dækker tab af den oprindelige datamappe, private nøgler og
stier, pauset kø, afbrudt kopiering og frigivelse samt bevarelse af nye
elevdata ved genforsøg. Den faktiske installergren afviser ændrede LAN-
adresser og porte i en PowerShell-prøve med simuleret runtime. Det er
softwareevidens; installationens UAC, LocalService, Windows-opgaver,
firewall, tillidslager og skolens hardware er fortsat ikke afprøvet her.
