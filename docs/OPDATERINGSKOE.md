# Opdateringskø i lærerens kontrolpanel

Kreditering: Klostermarksskolen.

Læreren åbner **Opdatér computere** i det eksisterende lærerpanel. Vælg en
kontrolleret udgivelse og computere, og vælg **Sæt valgte computere i kø**.
Linux-elever, Windows-lasercomputeren og Windows-hovedserveren vises som
adskilte roller. En
Linux-VM er en elevcomputer. Windows-lasercomputeren er et lærerværktøj.
Administration virker også uden elevcomputere.

SSH starter på hovedsystemets Windows-computer: inde i Windows-serverens VM,
hvis den centrale server er en VM; på den fælles Windows-computer i opsætningen
med alt på én computer. Browseren og lærerens eventuelle anden pc starter
ingen SSH-forbindelser. Panelet tager aldrig imod scripts, pakkeuploads,
serverstier, SSH-adresser eller private nøgler.

Den nye GitHub-integration kan klargøre katalogposter fra kontrollerede
release-pakker. Hovedserverens egen opdatering udføres lokalt af en separat
Windows-opgave; den bruger ikke SSH til sig selv. Læreren vælger stadig,
hvilken udgivelse og hvilke computere der skal i kø. Se
[programopdateringer via GitHub](GITHUB-OPDATERINGER.md) og
[controllerens engangsopsætning](../scripts/github_update_service/README.md).
Controllerens rettigheder og opgavestart er prøvet i et isoleret miljø på
skolens Windows-server-VM. Den er endnu ikke installeret til daglig drift.

Panelet viser den senest bekræftede installerede version. Køen gemmer den
først efter efterkontrol af installationen; download og klargøring tæller
ikke som installation. Almindelig opdatering afviser samme eller ældre
version. En gammel installation med ukendt version kræver lokal
IT-verificering og eventuelt `baseline_version` i den beskyttede profil.
Tidligere købestillinger og historik bevares ved import af nye udgivelser.

Slukkede computere bevares i køen. **Pause** holder næste forsøg tilbage;
et igangværende sikkert trin færdiggøres. **Prøv computeren igen** bruger
aktuel køversion og afviser et forsøg, der stadig kører. En uklar HTTP-fejl
kan genforsøges med samme request-ID gennem knappen **Prøv samme anmodning
igen**. **Klargjort · afventer genstart** er ikke en afsluttet opdatering;
den nye version skal bekræftes efter normal genstart. Eleven gemmer sit
arbejde først. Der sendes ingen automatisk genstart fra panelet. På en ældre elevinstallation kan første
opstart give serverkontakt og klargøring; først næste normale genstart anvender
den klargjorte udgivelse. Det er to adskilte opstarter, når maskinen først
kommer online ved den første.

## Hvis en elevcomputer slukkes under opdatering

Elevskærmen viser en tydelig besked, når en opdatering er klar til normal
genstart, og under installation og gendannelse. Under selve installationen
viser skærmen trin og forløbet tid. Lad computeren være tændt; programmets
almindelige sluk-/genstartsknapper afviser handlingen, mens opdateringen arbejder.
En fysisk afbrydelse af strømmen kan stadig afbryde computeren.

Tænd normalt igen efter en afbrydelse. En færdig klargøring kan fortsætte;
en afbrudt ændring af programfiler går gennem kontrolleret gendannelse af den
tidligere version. Køen må først vise afsluttet, når den valgte version og
dens filer er bekræftet. En gendannet tidligere version er ikke en gennemført
opdatering.

Ved **Gendannelse kræver hjælp** skal computeren forblive tændt, og læreren
skal kontakte IT. Bevar opdateringens journal, sikkerhedskopi og elevfiler;
en nulstilling eller sletning af køen er ikke den normale løsning.
Vedvarende fejl vises med en forklaring i lærerens kontrolpanel.

Skærm- og gendannelsesrettelser følger med nye, gennemgåede programversioner.
Ældre installerede eller allerede klargjorte opdaterere får dem først efter
en kontrolleret opdatering. Kildeprøver er ikke bevis for installation på skolen.

## Serveropsætning for skolens IT

Hubben bruger sin eksisterende konfigurerede datamappe (`--data`).
Undermappen `updates` indeholder `catalog.json`, `targets.json` og
`queue.sqlite3`. På Windows skal datamappen, SSH-nøgler, katalog, målkonfiguration
og workerfiler være beskyttet af skolens installations-ACL mod skrivning fra
elever og almindelige uvedkommende brugere. Programmet afviser symlinks og
reparse points i disse stier. Unix-rettigheder sættes til 0700/0600 for køen.
Der indføres ingen separat agentadgang eller agentnøgler.

Kataloget har formen:

```json
{"schema":1,"releases":[{"id":"linux-full-20261020","role":"linux_student","version":"2026.10.13","reviewed":true,"worker_root":"C:\\beskyttet\\linux-worker","manifest_sha256":"SHA256-AF-CONFIG.JSON"}]}
```

`manifest_sha256` er SHA-256 af de nøjagtige bytes i workerens endelige
`config.json`, efter at `admin_control` er sat. Workerens egen konfiguration
binder de gennemgåede payloads og hjælpere med hashes. Kataloget kontrolleres
igen før dispatch; en ændring blokerer en allerede bestilt opdatering.
Et tilstedeværende katalog er ikke i sig selv evidens for fysisk installation.

Målkonfigurationen har formen:

```json
{"schema":1,"targets":[{"id":"<ELEV-VM>","name":"Værksted 01","role":"linux_student","worker_target":"<ELEV-VM>","release_ids":["linux-full-20261020"],"identity_file":"C:\\beskyttet\\enheder.json","identity_sha256":"64-TEGNS-TILMELDINGSDIGEST","ssh_key":"C:\\beskyttet\\deployment_key","known_hosts":"C:\\beskyttet\\<ELEV-VM>.known_hosts","host_key_alias":"<ELEV-VM>"}]}
```

Linux-identiteten skal matche den aktuelle tilmeldingsfils
`enheder[identity_name eller worker_target].token_sha256` (alternativt
`token_digest`). Tilstedeværelse i statusregistret er utilstrækkeligt.
Den fjernkørende Linux-worker kontrollerer derudover computerens identitetsbevis.
For en endnu ikke kontaktet Linux-elevcomputer kan IT sætte
`host_key_policy: enrollment_proof`. Den gennemgåede Linux-transport må da
fastholde første værtsnøgle efter et gyldigt HMAC-bevis fra den aktuelle
tilmeldingsidentitet, før nogen payload sendes. Panelet viser dette som en
særskilt opsætning. En eksisterende værtsnøgle erstattes ikke automatisk.
Denne undtagelse gælder aldrig Windows-lasercomputeren.

En Windows-lasercomputer bruger `role: windows_laser_teacher` og en
forhåndsgodkendt fastholdt SSH-værtsnøgle. Den kræver ikke en opdigtet
Linux-tilmelding. Rollen vises som **endnu ikke verificeret**, indtil den
faktiske fjernopdatering har kontrolleret stationsinstallationens rolle.
En vellykket SSH-forbindelse er ikke i sig selv rolleevidens.

## Kontrakt til de eksisterende serveropgaver

Workerens beskyttede konfiguration bruger:

```json
{"admin_control":{"data_root":"C:\\beskyttet\\hub-data","release_id":"linux-full-20261020","module_path":"C:\\installeret-hub\\update_admin.py"}}
```

Importér `UpdateAdministration(data_root)` fra denne fil. Før hvert mål
kaldes `worker_allowed(release_id, worker_target)`. `False` betyder ingen
bestilling, pause, blokering eller et andet igangværende forsøg. Ingen
SSH-overførsel må starte uden `True`. `worker_job(...)['id']` giver det
vedvarende 32-tegns operation-ID til den separate Windows-laserworker.

Workerens `heartbeat(release_id)` kaldes ved hver kørsel før online-, pause-,
afslutnings- eller tidsfiltre. Den attesterer både klargjorte credentials og
udgivelsens nøjagtige konfigurationshash. Derfor kan HTTP-tjenesten vise status
og gemme lærerens bestilling, selv om den kører som LocalService og ikke må
læse SYSTEM-workerens konfigurationsmappe eller private nøgle. Attestationen
er bundet til det aktuelle beskyttede katalog/mål og udløber efter 24 timer.
Workerens dispatch gentager altid de direkte fil- og identitetskontroller;
en offentlig attestering alene giver ikke adgang til en SSH-overførsel.

Windows-laserworkerens beskyttede konfiguration kan bruge
`install_path_policy: user_default`, der vælger SSH-brugerens faktiske
`LOCALAPPDATA\Laserstation` på fjerncomputeren. Alternativt bruges en
kontrolleret absolut `install_path`. Ingen bestemt brugermappes stavemåde
gættes. Den eksisterende stationsmarkør og de forventede stationsfiler skal
være til stede før opdatering. Et fravær giver blokering, ikke en ny installation.

Rapportér kontrollerede stadier med `report(release_id, worker_target, status)`.
Status tillader `pending`, `waiting_for_computer`, `checking`, `transferring`,
`applying`, `staged_awaiting_restart`, `complete`, `profile_missing`,
`failed_rolled_back`, `recovery_required`, `retry`, `waiting_for_idle` og
`station_update_failed`. Rå SSH-fejl eller output sendes ikke til lærerens API.
For laser må `role_verified=True` kun sendes efter faktisk rollekontrol;
en completion uden bevis blokeres. Workers fortsætter kontrol af klargjorte
elevcomputere, så en efterfølgende opstartskvittering kan afslutte køen.

GitHub-controlleren sender desuden `installed_version` og `package_sha256`
ved afslutning; værdierne skal svare til den valgte katalogpost. Serverrollen
kræver også `role_verified=True`. Udløbne leases og ændrede beskyttede
konfigurationer kan ikke afslutte en bestilling. `version_unknown` og
`version_not_older` forklares i lærerens panel og kræver ingen geninstallation.

SQLite-transaktioner holder bestillinger, pause, versionskontrol og audit
samlet. Den atomiske lease gælder i op til tre timer, hvilket dækker den
lange Linux-overførsel og klargøring. En anden worker kan ikke overtage
en aktiv lease. En afbrudt worker kan derfor medføre ventetid, før nyt
forsøg tillades. Workeropgaver bruger også deres eksisterende proceslås.

API'et er `/api/updates/state` og `/api/updates/commands`, kun gennem lærerens
normale kontrolpanelsession. Netværksadgang kræver HTTPS. POST kræver JSON
og panelets Origin; bearer-tokens er ikke en adgangsvej. API'et udleverer
hverken private nøgler, tilmeldingsdigests, lokale serverstier eller workerkommandoer.

## Verificeringens omfang

`test_update_admin.py` afprøver bl.a. vedvarende kø, samtidige workers,
idempotens, tilmeldingsændring, katalogændring, atomisk batch, pause/genoptag,
forældede versioner, gamle workerresultater, staging/genstartsskillelinje,
Windows-laserens rollebevis samt HTTP-login og Origin-grænsen. Disse prøver
starter ingen SSH-forbindelser eller installationer. Den faktiske worker,
serveropgave, netforbindelse, Linux-opstart og fysiske laser skal have egen
evidens. Der er ikke bygget en ny ISO eller Windows-pakke til denne kodeprøve.

