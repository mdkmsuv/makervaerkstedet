# Programopdateringer via GitHub

Kreditering: Klostermarksskolen og Mathias Kusk Damgaard.

## Afgrænsning

Den valgte udgivelseskilde er
[`mdkmsuv/makervaerkstedet`](https://github.com/mdkmsuv/makervaerkstedet).
Kildekoden forbinder udgivelseskontrol, pakkedownload, beskyttet import,
lærerens opdateringskø og særskilte installationsadaptere. Den daglige drift
kræver hverken AI, Codex, en åben browser eller GitHub-login på skolens
computere. Hovedserveren skal køre og have internet for at hente nye releases;
allerede klargjorte opdateringer kan fortsætte uden GitHub-forbindelse.

Den offentlige kildeudgave skal bruge generiske Bauhaus-inspirerede skins.
Lokale skoletemaer, nøgler, konfigurationer og købestillinger bliver på
skolens system. En release skal være versionsmærket, hashkontrolleret og
godkendt efter [public-release-vejledningen](PUBLIC-RELEASE.md). Download,
import, installation og bekræftelse af installeret version er separate trin.

Følgende er adskilte trin:

| Trin | Aktuel funktion |
| --- | --- |
| Finde en offentlig udgivelse | Implementeret; den valgte GitHub-kilde kontrolleres én gang i døgnet eller efter lærerens bestilling. |
| Hente en valgt ZIP | Implementeret; størrelse og SHA-256 kontrolleres, før pakken markeres hentet. |
| Importere pakken til skolens opdateringskatalog | Den separate controller bekræfter pakken mod GitHub igen, kontrollerer arkivet og opretter lokale worker-konfigurationer. |
| Installere den nye version | Læreren vælger udgivelse og computere. Linux bruger næste normale opstart, laser bruger sin eksisterende stationsopdatering, og den eksisterende flade Windows-server bruger sin lokale opdateringsadapter. |
| Bekræfte installeret version | Køen gemmer den senest bekræftede version efter kontrolleret completion. Download og klargøring er ikke installationsbevis. |
| Publicering og samlet driftsprøve | Kræver kontrollerede byggeinputs, release og separat installationsafprøvning. |

Panelet siger **Udgivelse tilgængelig**, når en udgivelse er fundet.
Det betyder ikke, at alle computere behøver den, eller at noget er installeret.
Ved forbindelsesfejl vises fejlen og eventuelt den senest kendte udgivelse.
Eksisterende skolekøer og deres bestilte versioner ændres ikke af feedet.

## Drift uden udviklingsmappen

GitHub skal levere programkode, byggeopskrifter og versionerede
installations- og opdateringspakker. Det installerede hovedsystem og
opdateringstjenesten skal bruge deres egne programmapper og runtimes;
den oprindelige udviklingsmappe må ikke være nødvendig for daglig drift.
Programmet kører på skolens computere, også når GitHub ikke kan nås.

Skolens skin er en privat del af installationen. Det samme gælder
konfiguration, tilmelding, certifikater og nøgler, maskinopsætning,
elevprojekter, køer, workflows og historik. Disse data skal bevares og
sikkerhedskopieres sammen med opdateringstjenestens lokale opsætning.
En ny download fra GitHub kan genskabe programfilerne, men ikke skolens data.
Se [backup og gendannelse](MAKERSPACE-BACKUP.md) og
[lokal skoleprofil](SKOLETILPASNING.md).

En udgivelse, der kan bygges uafhængigt af udviklingsmaskinen, kræver:

- Et rent checkout af den valgte Git-version med nødvendige byggefiler og
  licenser. Arbejdsnotater, pilotdata og lokale testmapper indgår ikke.
- Tilgængelige, gennemgåede eksterne inputs med fast version og SHA-256,
  herunder Windows-runtime, CaDoodle, Scratch, Datatræner og Matchbox.
  En sti til en udviklers eksisterende programmappe er ikke tilstrækkelig.
  Store binære inputs kan ligge som release-assets.
- Særskilte pakker og prøver til ren Linux-installation, Linux-opdatering,
  ren Windows-serverinstallation og Windows-serveropdatering. Laserstationens
  installation og opdatering bevarer også deres særskilte indgange.
- En prøve fra en ren byggemaskine uden tidligere caches samt en faktisk
  installations- og opdateringsprøve, der bekræfter version og bevaring af
  lokal skoleprofil og driftsdata.

Det nuværende releaseworkflow bygger **opdateringspakker**. Det bygger
ikke en ny Linux-ISO eller en komplet Windows-installationspakke.
Linux og server kræver stadig eksterne byggeinputs; kildeaudit og tests med
syntetiske inputs beviser ikke, at disse kan hentes eller at en offentlig
release kan installeres. Udviklingsmappen kan først undværes helt, når de
ovenstående forløb er afprøvet med de faktiske udgivelsesfiler.

## Lærerens forløb

Under **Opdatér computere → Programopdateringer** vælges projektets officielle
GitHub-lager som `ejer/projekt`. Valget foretages én gang. Et senere skift
kræver hjælp fra skolens IT; browseren modtager aldrig private nøgler eller
vilkårlige downloadadresser.

**Søg efter opdateringer** gemmer en bestilling til hovedserverens worker.
Når der findes en gyldig offentlig udgivelse, vises version, udgivelsesnoter
og de relevante Linux-elev-, Windows-laser- eller Windows-serverpakker.
**Hent og klargør opdatering** gemmer en downloadbestilling. Den separate
controller importerer pakken, når den kan bekræfte udgivelsen og den lokale
opsætning. Vælg derefter **Vælg udgivelsen i opdateringskøen**, markér
computerne og sæt dem i kø. Valg af udgivelsen alene bestiller ingen installation.

En uklar netværksfejl kan genforsøges med samme anmodnings-ID. Slukkede
computere bliver i køen. En eksisterende uafsluttet opdatering erstattes
ikke automatisk af en nyere udgivelse. Almindelig opdatering afviser samme
eller en ældre version end den senest bekræftede; en planlagt gendannelse er
en særskilt handling. Ingen elevcomputer genstartes automatisk.

Ved første overgang fra en gammel installation skal den installerede version
kunne bestemmes. Mangler en entydig versionsmarkør, kontrollerer skolens IT
installationen og angiver `baseline_version` i den beskyttede lokale profil.
Ukendt version blokerer installationen; controlleren gætter ingen baseline.
En ændret pakke skal have en ny version, også når datoen er den samme.

Hovedserveren vises i sin egen rolle. Læreren får besked om, at kontrolpanelet
bliver kortvarigt utilgængeligt. Serveradapteren kontrollerer aktive print-
og laseropgaver, før programmet stoppes. Kodeprøver af dette forløb er ikke
bevis for en faktisk Windows-installation.

## Server og GitHub-kontrakt

Feedets offentlige opsætning ligger i hubbens eksisterende datamappe under
`updates/feed.json`:

```json
{"schema":1,"repository":"mdkmsuv/makervaerkstedet","enabled":true}
```

Eksemplet angiver den valgte offentlige kilde; det er ikke i sig selv en
ændring af en installeret servers konfiguration.
`updates/feed.sqlite3` gemmer kontrolstatus, anmodnings-ID'er, audit og
downloadstatus. Hovedserverens worker bruger sin egen beskyttede stagingmappe.
Alle stier gives af installationen; ingen skolespecifikke adresser eller
personlige brugermapper er indbygget i feedet.

I den nye kildekode starter hubben selv sin baggrundsrunner. Den bruger
datamappens `updates/downloads` som staging og stopper sammen med hubben.
Der er ingen GitHub-forbindelse, før en kilde er valgt. Ved lukning annulleres
igangværende arbejde; et sent netværkssvar må ikke blive en ny klar pakke.
Denne runner er nu deployet og kontrolleret på den eksisterende server-VM.
Der er stadig ingen download- eller installationsprøve med en offentlig
release fra det valgte repo.

Den valgfrie selvstændige worker-indgang til anden serverdrift er:

```text
python -B release_feed.py --data-root "<DATA_ROOT>" --staging-root "<STAGING_ROOT>"
```

Begge pladsholdere erstattes med absolutte serverstier. `--once` udfører én
kontrolrunde. Uden dette flag kører workeren med en proceslås og kontrollerer
arbejdsbestillinger hvert 30. sekund; et almindeligt GitHub-opslag sker én gang
i døgnet. Læreren kan altid bestille en ny kontrol med **Søg efter opdateringer**.
Den deler proceslås med hubbens indbyggede runner, så de ikke
begge udfører samme feedarbejde. Browseren behøver ikke være åben. Hverken
hubrunner eller separat feedworker behøver SYSTEM eller SSH-nøgler. Den
privilegerede import kører i den separate controller med egen genkontrol.

## Den separate opdateringstjeneste

[`scripts/github_update_service/README.md`](../scripts/github_update_service/README.md)
beskriver engangsopsætningen for skolens IT. Controlleren installeres som
en særskilt Windows-opgave med sin egen beskyttede kode og Python-runtime
uden for de programmapper, den opdaterer. Kataloget og computerlisten
udvides; eksisterende bestillinger og historik bevares.

Controllerens beskyttede konfiguration fastlåser repository og lokale
transportprofiler. Den stoler ikke på et SHA-256-felt i downloaddatabasen:
pakken bindes igen til metadata fra det konfigurerede GitHub-repository.
Den importerede pakke indeholder ingen lokal SSH-konfiguration. Lokale
worker-konfigurationer fremstilles af controlleren fra skolens profiler.
Programmet viser, om klargøringstjenesten har meldt sig; liveness er ikke
evidens for en gennemført installation.

Den første serveradapter understøtter det eksisterende `school-flat-v1`-
layout med ElevhubServer og Printværksted. Et andet layout afvises, så det
ikke behandles som en skjult geninstallation. `generation-v1` har sit eget
vedligeholdelsesforløb og er ikke koblet til denne adapter.

Workeren bruger `https://api.github.com/repos/{ejer}/{projekt}/releases/latest`
og `X-GitHub-Api-Version: 2026-03-10`. Offentlige udgivelser kan læses uden
GitHub-login. Draft og prerelease afvises. Se GitHubs dokumentation for
[release-endpointet](https://docs.github.com/en/rest/releases/releases)
og [API-versionering](https://docs.github.com/en/rest/about-the-rest-api/api-versions).

Udgiveren vedhæfter filen **makervaerkstedet-update.json** og de selvstændige
ZIP-pakker til den samme GitHub-release. GitHubs automatisk genererede
source-arkiv er ikke et opdateringsmanifest. Hver asset skal være under
2 GiB; større pakker kræver et andet pakkedesign. Se
[GitHubs grænser for releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

Manifestets felter er:

```json
{
  "schema": 1,
  "product": "Makervaerkstedet",
  "version": "2026.10.20",
  "notes": "Udgivelsens ændringer og relevante krav.",
  "packages": [
    {"role":"linux_student","asset":"linux-update.zip","sha256":"<SHA-256>","size":12345,"format":"linux-queue-v1"}
  ]
}
```

`<SHA-256>` og størrelsen er forklarende pladsholdere; udgivelsesværktøjet
beregner de faktiske værdier. Hver rolle må forekomme én gang:

| Rolle | Format |
| --- | --- |
| `linux_student` | `linux-queue-v1` |
| `windows_laser_teacher` | `laser-code-v1` |
| `windows_server` | `school-server-v1` |

Versionen er `ÅÅÅÅ.MM.DD` med et eventuelt nummer som fjerde led.
Assetnavne er almindelige ZIP-basenavne uden stier. Størrelse og SHA-256
skal passe nøjagtigt. Downloadadresser udledes af GitHubs konkrete assets;
manifestet må ikke angive egne URL'er. Kun HTTPS og afgrænsede GitHub-hosts
accepteres, også ved omdirigeringer. Metadata er størrelsesbegrænsede.

## API til lærerpanel og importintegration

`GET /api/updates/feed/state` udleverer renset kontrolstatus. `POST
/api/updates/feed/commands` bruger lærerens almindelige session, JSON og
panelets Origin. Bearer-tokens er ikke en adgangsvej. En kommando er:

```json
{"request_id":"<32 små hex-tegn>","action":"check","params":{}}
```

Handlingerne er `configure` med `repository`, `check` uden parametre og
`download` med `release_id` og `asset`. Der er ingen upload-, sti-, script-
eller SSH-kommando fra browseren.

En kontrolleret serverintegration kan kalde `ReleaseFeed(data_root).tick(staging_root)`.
På samme instans returnerer `ready_packages()` pakker, der er hentet og
kontrolleret igen. Efter procesgenstart gives stagingroden udtrykkeligt som
`ready_packages(staging_root)`. Den gemte databases filsti bliver ignoreret;
workeren udleder stien fra den betroede stagingrod og validerer igen rolle,
format, lager, asset, størrelse og SHA-256. Pakkerne skal stadig importeres
af den særskilte installationsintegration. `mark_outcome(download_id,
'imported'|'blocked', code)` gemmer dens kontrollerede udfald. `imported`
betyder ikke installeret.

## Fremstilling af publicerbart manifest

[`scripts/build_update_manifest.py`](../scripts/build_update_manifest.py)
skriver kun metadata for allerede fremstillede ZIP-filer med uafhængige,
lokalt verificerede SHA-256-pins. Scriptet bygger ingen ISO eller stor pakke
og uploader intet. Før output kaldes kildeeksportens privacy-audit. ZIP'ens
indhold kontrolleres også for private felter og driftsdata.

Nye ZIP-formater har en rodfil `update-package.json` med `schema`, `role`,
`format`, `version` og `files`, hvor `files` er en fuldstændig tabel fra
relative filnavne til SHA-256. Inventory, rolle og version skal passe;
`CREDITS.md` er påkrævet. En afgrænset adapter accepterer også laserens
eksisterende `manifest.json` med formatet
`makervaerkstedet-station-code-v1`.

Ukendte binære medlemmer kræver selvstændig offentlig review og eksplicitte
immutable binary-pins; en teknisk installationsprøve alene er ikke en
privacy-godkendelse. Privatnøgler, skoleidentiteter, lokale køer og backups
er ikke udgivelsesfiler. Kildekontrollen og manifestbuilderen erstatter
hverken importintegrationens bevaringsprøver eller en reel installationsprøve.

## Fremstilling af rollepakker og GitHub-release

[`scripts/build_github_release.py`](../scripts/build_github_release.py)
bygger versionsbundne rollepakker fra en kontrolleret kildeudgave og
eksplicitte byggeinputs. Den skriver ZIP-filer, `makervaerkstedet-update.json`,
`release-plan.json` og `SHA256SUMS.txt` til en ny outputmappe.
Planen er markeret `validated_local_only`; den lokale builder uploader intet.

Eksempel fra en privacy-godkendt kildeudgave:

```powershell
python -B scripts/build_github_release.py `
  --source-root . `
  --repository mdkmsuv/makervaerkstedet `
  --version 2026.10.21 `
  --notes .\release-notes.txt `
  --scope selected `
  --build-input-manifest .\laser-inputs.json `
  --output-dir .\publisher
```

Her indeholder `laser-inputs.json`:

```json
{"schema":1,"packages":{"windows_laser_teacher":{}}}
```

Eksemplet bygger kun laserens kodepakke, som genbruger den eksisterende
stations runtime. Derfor vælges `--scope selected` udtrykkeligt.
Standardvalget er `--scope system`, som kræver alle tre opdateringsroller.
Mangler en rolle, stopper byggeren før fremstilling af pakker og manifest.
En samlet opdatering kræver derfor et udvidet inputmanifest.
JSON-filens `schema` er `1`; `packages` indeholder de ønskede roller:

- `linux_student.external`: `deb`, `bootstrap`, `kloster`, `scratch`,
  `datatraener` og `matchbox`.
- `windows_server.trusted_runtime`: den eksplicit gennemgåede runtimekilde.
- `windows_laser_teacher`: et tomt objekt er tilstrækkeligt.

Hvert eksternt input angiver `path`, `sha256` og `public_review`.
Opaque binære medlemmer kræver selvstændige pins i
`approved_binary_sha256`; en ydre arkivhash gør ikke skolens private filer
egnede til offentliggørelse. Den valgte version bindes til Linux-payloadens
releasefil i en midlertidig byggemappe, så kildetræet ikke overskrives.
Programbyggeren kontrollerer også indholdet inde i Linux-payloaden.

Et allerede gennemgået inputarkiv kan hentes med `--build-inputs-url` og
`--build-inputs-sha256`. Adressen skal være en HTTPS-release-asset i det
valgte GitHub-repository. Inputarkivet indeholder `build-input-manifest.json`
og de beskrevne filer; private netværksadresser og legitimationsoplysninger
skal ikke med.

Workflowet `.github/workflows/release.yml` kan startes fra GitHubs **Actions**.
Angiv version, udgivelsesnoter og omfang. Standardvalget `system` kræver et
inputarkiv med SHA-256 og inputs til alle tre roller. Valget `laser` bygger
kun laserens kode fra repositoryet og bruger intet eksternt inputarkiv.
Manglende systeminputs skifter aldrig automatisk til en laserudgivelse.
Kildeaudit, målrettede tests og pakkekontrol skal lykkes først. Det valgfrie
`publish_draft` opretter en **draft release** med de kontrollerede filer.
Et draft bliver ikke tilbudt skolens computere. Udgiveren skal først
kontrollere pakkerne og publicere releasen som stabil.

Et almindeligt commit udgiver ingen installerbar opdatering. En stabil
release kan indeholde en eller flere roller; en laserrelease ændrer ikke
automatisk elevcomputere eller hovedserver. Opdatering af eksterne runtimes,
modeller og tredjepartsprogrammer kræver tilsvarende gennemgåede byggeinputs.
Operativsystemernes egne sikkerhedsopdateringer er et separat driftsforløb.

## Kildeprøver

De nye feed-tests afprøver bl.a. døgnkontrol, ærlige internetfejl,
rolle-/assetbinding, redirect-grænser, databasemanipulerede stier, hash og
størrelse ved streaming, genoptagelse samt lærerens login-/Origin-grænse.
Manifesttests afviser forkerte pins, korrupt ZIP, private felter og forkert
rolle/version. Disse prøver bruger små neutrale fixtures og ingen rigtig
GitHub-publicering, skolecomputer eller fysisk maskine.
