# Opdatér en eksisterende Windows-server

Kreditering: Klostermarksskolen og Mathias Kusk Damgaard.

En opdatering ændrer programmerne i et eksisterende Makerværksted og bevarer
skolens opsætning og data. Ren installation, genoptagelse af en afbrudt
installation, tilbageførsel og backupgendannelse er særskilte handlinger.
Brug aldrig `START.cmd` eller `Setup-School.ps1 -Resume` som versionsopdatering.
En Windows-server i en VM følger samme vej som en fysisk Windows-server.

## Vælg det rigtige installationsformat

| Eksisterende format | Opdateringsvej |
| --- | --- |
| `school-flat-v1`: ElevhubServer og separat Printværksted på de faste Windows-stier | GitHub-controllerens serveradapter eller den lokale `Invoke-SchoolUpdate.ps1` nedenfor. |
| `generation-v1`: fælles program-/datavalg i `control/active-generation.json` | Installationens `Maintain-School.cmd`; se det særskilte afsnit nedenfor. |
| Andet eller ukendt layout | Stop og få installationen identificeret. Ingen af værktøjerne må bruges som skjult geninstallation. |

Linux-elevcomputere og Windows-laserstationen har egne opdateringspakker og
indgange. Der findes ingen understøttet Windows-elevklient.
Se [opdateringskøen](OPDATERINGSKOE.md) og
[valg af installationsvej](KOM-GODT-IGANG.md#vælg-først-ren-installation-eller-opdatering).

## Normal opdatering gennem lærerens panel

Følg [GitHub-opdateringer](GITHUB-OPDATERINGER.md) for engangsopsætningen af
den separate opdateringstjeneste og lærerens kø. Den valgte udgivelseskilde er
[`mdkmsuv/makervaerkstedet`](https://github.com/mdkmsuv/makervaerkstedet).

1. Vælg **Opdatér computere → Programopdateringer → Søg efter opdateringer**.
2. Kontrollér version og udgivelsesnoter, og vælg **Hent og klargør opdatering**.
3. Vælg udgivelsen i opdateringskøen, markér hovedserveren og bestil opdateringen.
4. Regn med, at kontrolpanelet er utilgængeligt, mens serveren opdateres.
5. Kontrollér den bekræftede installerede version og driften, før køerne åbnes.

Et almindeligt commit er ingen installationsbestilling. Fundet, hentet og
importeret pakke er heller ikke bevis for installation. Controlleren kræver
en entydig eksisterende version; skolens IT skal kontrollere og angive en
beskyttet `baseline_version`, hvis en gammel installation mangler markøren.
Samme eller ældre version afvises som almindelig opdatering.

Private SSH-nøgler, scripts og vilkårlige serverstier hører ikke til i
browseren. Controlleren bruger skolens beskyttede lokale transportprofiler.
En stabil, kontrolleret release og en faktisk installationsprøve er stadig
nødvendige; denne vejledning attesterer ingen bestemt offentlig release.

## Før serveren stoppes

- Gem igangværende arbejde, og afklar alle aktive print- og laseropgaver.
  Adapteren afviser aktive hardwarejob; et job med usikker maskinstatus skal
  afklares ved udstyret, før opdateringen fortsætter.
- Opret og kontrollér en separat beskyttet backup efter
  [backupvejledningen](MAKERSPACE-BACKUP.md). En opdateringsjournal på samme
  disk erstatter ikke backup mod diskhavari.
- Kontrollér kilde- og målversion, pakkens oprindelse og SHA-256 samt plads til
  programkopi, data, migrationsprøve og fejlhåndtering. Den lokale adapter
  kræver mindst 1 GiB ledigt, men større installationer kan kræve mere.
- Bevar skolens lokale profil og tema. Udgivelsens generiske Bauhaus-tema må
  ikke erstatte skolens valgte udseende. Krediteringen bevares på tværs af skoler.

## Lokal opdatering af `school-flat-v1`

Dette er en teknisk indgang til skolens IT. Adapteren er afgrænset til
`C:\Program Files\ElevhubServer`, `C:\Program Files\Printvaerksted`,
`C:\ProgramData\ElevhubServer` og
`C:\ProgramData\Printvaerksted\data\school`.
Den kræver de eksisterende opgaver `ElevhubServer`, `Printvaerksted`,
`PrintvaerkstedWeb`, `Printvaerksted-Kameralys` og `Printvaerksted-Recovery`.
Andre stier eller opgaveopsætninger kræver særskilt integrationsarbejde.

Brug en kontrolleret kandidat med `candidate.json`. Kandidatens runtime skal
være verificeret mod den betroede pakkes `integrity.json`. Installeret
`python.exe`, `python314.dll` og `python314.zip` skal have de forventede,
identiske hashes. Denne vej skifter ikke interpreterens ABI.
Se [adapterens vejledning](../scripts/school_server_update/README.txt).

Den beskyttede stagingmappe skal indeholde:

```text
candidate/
update.py
Invoke-SchoolUpdate.ps1
Repair-SchoolRuntimeAccess.ps1
```

Eksemplet bruger lokale pladsholderstier. Erstat dem med skolens beskyttede
stagingmappe og en ny journalsti. `$candidateSha` skal komme fra den
kontrollerede udgivelse; beregning af en hash fra en ukendt download beviser
ikke udgiverens identitet.

```powershell
$staging = 'C:\ProgramData\MakervaerkstedetVedligehold\staging'
$candidateSha = '<64 små hextegn fra den kontrollerede udgivelse>'
$journal = 'C:\ProgramData\MakervaerkstedetVedligehold\server-opdatering-01'

# Kontrollér kun kandidaten; ændrer ikke installationen.
& "$staging\Invoke-SchoolUpdate.ps1" `
  -Candidate "$staging\candidate" -Sha256 $candidateSha -Journal $journal

# Kør derefter fra en forhøjet administrator-/SYSTEM-session.
& "$staging\Invoke-SchoolUpdate.ps1" `
  -Candidate "$staging\candidate" -Sha256 $candidateSha -Journal $journal -Apply
```

Brug processpecifik `ExecutionPolicy Bypass`, hvis skolens kontrollerede
kørsel kræver det; ændr ikke computerens permanente politik. Genbrug eller
overskriv aldrig en eksisterende journal. Kommandoen er ingen resume- eller
restore-indgang. Kandidatkontrollen uden `-Apply` er ikke en fuld prøve af
Windows-rettigheder, opgaver eller efterfølgende drift.

Forløbet gemmer opgavernes XML, ejere, kommandoer og oprindelige
aktiveret-/kørende-tilstand. Det stopper processerne kontrolleret, holder
alle fem tjenestedatalåse, tager SQLite-backup inklusive committed WAL og
afprøver migration på en kopi før programændringen. Eksisterende rækker,
kolonneværdier og datafiler skal bevares. Installationens to understøttede
`integrity.json`-formater bevares.

Konfiguration, identiteter, tilmeldinger, nøgler, printer-/laseropsætning,
workflows, køer, historik og lokal skoleprofil skal bevares. Den snævre
adgangshjælper sikrer LocalService-adgang til de nødvendige låse og
protokolmetadata; den udvider ikke adgangen til hele dataroden eller
sikkerhedsmapperne. Interne bridge-nøgler er beskyttede lokale tjenestenøgler.

## Kontrollér resultatet, før køerne åbnes

Den lokale wrapper gendanner opgavernes kvitterede tilstand, kontrollerer
forbindelse til tjenesteportene og afslutter først journalen efter
versions-, fil- og bevaringskontrol. Printkøen holdes pauset til lærerens
kontrol. En portkontrol alene dokumenterer ikke korrekt panel, TLS eller
fysisk udstyr.

Den installerede kildeopdatering attesteres særskilt i `school-update.json`
og kandidatens manifest; den gamle pakkemarkør kan fortsat stå som baseline.
Fra den samme bevarede stagingmappe kan skolens IT efterprøve den installerede
version og filerne:

```powershell
& "$staging\candidate\hub\runtime\python.exe" -B "$staging\update.py" installed `
  --candidate "$staging\candidate" --sha256 $candidateSha
```

Kontrollér også lærerlogin, HTTPS med skolens CA, elevforbindelser,
maskinopsætning, workflows, køhistorik og skolens valgte tema.
Gem version, kandidatens SHA-256, journal, helbredsresultat og den konkrete
bevaringskontrol. Upload aldrig journaler, backups eller private skoledata
som releasefiler.

Gendan kun den tidligere printpause gennem den autentificerede køfunktion
efter kontrollen. En pauseændring frigiver ingen plade. Fysisk rydning,
printstart og laserproduktion er selvstændige handlinger, som prøves på
skolens faktiske udstyr.

## Fejl, tilbageførsel og gendannelse

**Fejl under den lokale program-/datatransaktion:** Adapteren forsøger at
føre program og data tilbage, mens tjenesterne er stoppet og datalåsene
holdes. Bevar journalen og eventuelle `failure-traceback.txt` og
`rollback-failure-traceback.txt`. En ufuldstændig tilbageførsel kan efterlade
opgaverne deaktiveret; genstart ikke blindt.

**Fejl efter programændringen eller genstart:** Wrapperen gendanner ikke
automatisk databaser. Der kan være kommet nye afleveringer. Bevar både de
aktuelle data og snapshot, undersøg journal og logs, og reparér den konkrete
fejl. Den lokale flade adapter har ingen generel kommando til tilbageførsel
efter genåbnet drift eller til genoptagelse af en afbrudt opdatering. Kør ikke
gammel programkode direkte på nye database- eller workflowtilstande.

**Backupgendannelse:** En ældre backup ruller også data tilbage og kan dermed
miste nyere arbejde. Den er en særskilt redningshandling og skal først
kontrolleres isoleret. Hosttab og ny Windows-reprovisionering er ikke løst af
opdateringsadapteren. Brug [backupvejledningen](MAKERSPACE-BACKUP.md) og
[installationsreparation](INSTALLATIONSREPARATION.md) til deres respektive
formål; førstegangsreparation er ingen versionsopdatering.

## Særskilt vedligeholdelse af `generation-v1`

Denne vej kræver en færdig `generation-v1`-installation og er ikke koblet til
GitHub-controllerens flade serveradapter. `-GenerationLayout` er en eksplicit
udviklingsindgang til første installation; den migrerer ikke en eksisterende
flad installation. Den separate Printværksted-installation understøttes
ikke sammen med dette generationslayout.

Installationens faste
`C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd`
bruger sin egen runtime. Den understøtter `update`, `restore`, `rollback`,
`resume`, `cancel` og `status`. Brug nye lokale destinationsmapper til journal
og permanent generation. Deres forældremapper skal findes og være
administratorbeskyttede; destinationerne må ikke findes eller overlappe.

```powershell
$maintain = 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd'
$runtimeRoot = 'C:\Program Files\ElevhubServer'
$operation = 'C:\ProgramData\MakervaerkstedetVedligehold\generation-opdatering-01'

& $maintain -Action update -RuntimeRoot $runtimeRoot -Folder $operation `
  -MaterializedFolder 'C:\ProgramData\MakervaerkstedetGenerationer\generation-ny' `
  -ProgramRoot 'D:\Kandidater\program'

& $maintain -Action status -RuntimeRoot $runtimeRoot -Folder $operation
& $maintain -Action resume -RuntimeRoot $runtimeRoot -Folder $operation
```

`status`, `resume` og `cancel` bruger kun den gemte journal og må ikke få
nye handlingsparametre. `cancel` er annullering af en uafsluttet operation,
ikke tilbageførsel efter genåbnet drift. Flyt eller redigér ikke mapper,
manifester, versionsvælger eller holdmarkører manuelt.

- `rollback` kræver en ny `-Folder`, ny `-MaterializedFolder` og
  `-CompletedFolder` med den afsluttede opdatering. Den bruger bevaret
  tidligere programkode sammen med en frisk kopi af de aktuelt valgte data.
  `-SettledFolder` er kun relevant, hvis en tidligere annullering genåbnede
  netop samme program/data med højere revision.
- `restore` kræver en ny `-Folder`, ny `-MaterializedFolder`,
  `-ProgramRoot` og `-BackupSource`. Backupen skal passe til samme
  installation, skole, netværkskontrakt og installerede CA. Det er en
  bevidst gendannelse af historiske data.

Klargøring med `state: prepared` eller `ready` og `activation_ready: false`
er ikke aktivering. En kompatibilitetskontrol med `compatible_at_check: true`
og `deployment_ready: false` er heller ikke installationsbevis.

Generationsvejen kræver byteidentisk runtime og faste startere samt et
komplet `makerspace-release.json`. Ny runtime eller launcher kræver en anden
installationsvej. Kun den kendte datakontrakt og den afgrænsede
print-/lasermarkering fra version 0 til 1 understøttes; ukendte versioner og
ældre skemaer afvises. Det er ingen garanti for fremtidige nedgraderinger.

Hostkontrollen kan afvise eksterne backends, separat laserkø, kørende Beam
Studio-/FLUX-processer, ukendte opgaver og utilstrækkelige rettigheder. Den
lukker ikke eksterne programmer på operatørens vegne. Printkøen genåbnes
ikke automatisk, og opdateringen starter ingen fysisk maskine.

Fuld Windows-afprøvning af generationslayoutets installation, rettigheder,
aktivering, restore, rollback, afbrydelser og afinstallation er fortsat
nødvendig før driftsklarhed kan bekræftes. Isolerede kodeprøver dokumenterer
hverken strømudfald på fysisk lagring eller skolens faktiske hardware.
