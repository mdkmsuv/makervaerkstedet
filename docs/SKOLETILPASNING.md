# Kodeagentens vejledning til skoletilpasning

**Kreditering: Klostermarksskolen.** Dette dokument beskriver, hvordan en
kodeagent tilpasser kildekoden til en skole. Makerværkstedet er lærerens
hovedsystem; Linux-elevcomputerne og Elevhub er delkomponenter. En tilpasning må
derfor bevare lærerens kontrolpanel, datakontrakter og eksisterende
sikkerhedskontroller. Dokumentet ændrer ikke en installation, starter ikke en
tjeneste og bygger ikke en pakke.

Kun Linux bruges til elevværkstederne. Windows bruges til serveren og
lærerens browseradgang; den tidligere Windows-elevklient er en lukket vej.

## Før arbejdet

Læs først [Makerværkstedets arkitektur](MAKERSPACE-ARKITEKTUR.md) og
projektets aktuelle kode og dokumentation. Denne vejledning er kun en indgang for en
kodeagent, der ændrer kildekode og tests; den er ikke en vejledning til en
AI-agent, der skal styre skolens daglige drift.
Afklar derefter, om opgaven gælder:

- en skoleprofil eller en eksisterende installation,
- en Windows-server med separat LAN,
- Linux-Elevhub som klient,
- en maskine, et workflow eller kun lærerens overblik.

Brug midlertidige testdata og en ny testmappe. Kopier ikke skolens
adgangsnøgler, elevdata eller private konfiguration ind i eksempler.
Brug relative kildehenvisninger og syntetiske værdier i tests. En opgave om
en konkret installation kan kræve læsning af dens konfiguration; hold den
adskilt fra generisk kildekode og dokumentation.

## Skoleprofilen

Windows-skolens `school.json` oprettes af
[Setup-School.ps1](../deployment/school-setup/Setup-School.ps1), ikke af
Makerspace-databasen. Scriptets profil har `schemaVersion: 1` og felterne
`school_name`, `theme`, `role`, `server`, `student_subnet` og
`admin_public_key`. `role` er `vm` eller `makerspace`; `theme` er en af de
godkendte farver. `enrollment.py` validerer profilen i
[`deployment/school-setup/enrollment.py`](../deployment/school-setup/enrollment.py)
og kræver blandt andet schema, serveradresse og et gyldigt IPv4-net.

`Setup-School.ps1` bruger `-SchoolName`, `-Role`, `-RoleIp`,
`-StudentSubnet` og `-Theme`. Ved eksisterende installation sammenligner det
profilen med ejerskabsmarkøren og afviser en uventet skole, rolle, server eller
subnet. En kodeagent skal ændre valideringen og skabelonen samlet, hvis
profilformatet ændres; den må ikke skrive en ny privat profil ud fra gæt.

## Lærerens skoleprofil

I det aktuelle lærerpanel vælges **Skoleprofil**. Udfyld skolens navn,
vælg et PNG-logo, en temafarve og en af de tilgængelige skrifttyper:
**Standard**, **Manrope** eller **Metrophobic**. Et nyt PNG må højst være
2 MB og 2048 × 2048 pixels; gennemsigtighed understøttes. Serveren
validerer og genkoder billedet, så skjulte billedmetadata ikke følger med.

Forhåndsvisningen viser navn, logo, farve og skrifttype før **Gem
skoleprofil**. Farven bruges som accent; systemet finder mørkere
knap-/tekstnuancer ved behov for læsbarhed. Skrifttypen gælder
brugerfladen, ikke skrifter inde i elevernes tegninger eller projekter.
De medfølgende lokale fonte kræver ingen ekstern fonttjeneste.

**Indlæs gemt profil** henter de senest gemte valg; fladen beskytter
ugemte ændringer med sin afslutningsdialog. Ved ukendt svar på gemning
bruges **Afklar samme gemning**, som genbruger samme kommando og indhold.
En anden administrator kan have ændret revisionen; indlæs da frisk
status og vælg på ny. En forhåndsvisning er ikke en gemt profil.

Kilden ligger i
[school_profile_admin.py](../system/linux-elevhub/server/elevhub/school_profile_admin.py)
og [school-profile.js](../system/linux-elevhub/server/elevhub/web/school-profile.js).
API'et er `GET/POST /api/admin/school-profile` med lærerlogin og samme
Origin. Det accepterer navn, farve, et af de faste fontvalg og logo-
handling; ingen vilkårlige serverstier, CSS eller downloadadresser.

### Distribution og offline

Den gemte administrerede profil ligger i hubbens private dataområde
under `local-appearance`; revisions-/gemmejournalen er
`school-profile-admin.sqlite3`. Profilen er ikke et app- eller
programartefakt.

Tilmeldte Linux-enheder modtager metadata og indholdsadresserede assets
via den eksisterende enhedsidentitet og serverens HTTPS/CA:
`/api/device/school-profile` og faste assetruter. Den native klient
kontrollerer revision, SHA256, størrelse og manifest, gemmer under
`/etc/elevhub/local-appearance` og skifter manifest atomisk.
Sidst validerede profil virker uden serverforbindelse og efter genstart.
Der kræves ikke en ny tilmelding eller en genstart af kioskbrowseren for
en almindelig profilændring.

Den relevante klientkode er
[school_profile_sync.py](../system/linux-elevhub/administration/school_profile_sync.py).
Appværtens `theme-v1` og de eksisterende temaindgange leverer skolens
validerede udseende til start, projekthylde og apps.
En pakkeopdatering må ikke erstatte lokal appearance eller dens nøgler.

### Eksisterende private profiler og offentlig standard

En tidligere filbaseret skoleprofil beholdes, indtil administratoren
gemmer formularen. Panellets besked viser denne tilstand. Gamle
profilfiler bevares også ved den første administrerede gemning.
En ulæselig eller konfliktende profil må ikke tavst overskrives.

Den offentlige udgave bruger de eksisterende Bauhaus-inspirerede skins
med `red`, `yellow` og `green`. Lokale skolelogoer, CSS, navn,
farver og fontvalg er private installationsdata. Kopiér dem ikke ind i
Git, eksempler, apppakker eller offentlig kildeeksport. Bevar kreditering
til Klostermarksskolen og Mathias Kusk Damgaard samt tredjepartslicenser.

Det eksisterende filformat har `schemaVersion: 1`, skoleidentitet og
en `style` med relative assetstier og SHA256. Læserne i
[system/app/school_appearance.py](../system/app/school_appearance.py) og
[hubben](../system/linux-elevhub/server/elevhub/school_appearance.py)
afviser links, ugyldige stier, for store filer og forkerte hashes.
En beskadiget appearance-mappe giver synlig fejl; den erstattes ikke
tavst af standardtemaet. Brug den eksisterende backup-/rollbackvej til
at bevare `school.json` og lokale appearance-mapper.

`school.json` har fortsat serverrolle, netværk og tilmelding som
beskrevet ovenfor. Formularen til udseende omkonfigurerer ikke disse
felter. Den tidligere `Manage-School`-farvefunktion og private
`brand`/`brand_dark` kan stadig være relevante for ældre installationer;
brug den leverede versions guide. En `.2`-installation får ikke det nye
profilpanel ved at redigere kildefilerne.

## Separat Windows-LAN og serverstart

Den Windows-server, der står på skolens LAN, konfigureres gennem
[`system/windows-elevhub/server/install.ps1`](../system/windows-elevhub/server/install.ps1).
De relevante valg er server-IP, elev-subnet, læreradresse og -port samt
`PrintBackendPort`, `DesignBackendPort`, `TeacherBackendPort` og eventuelt
`UseExistingPrintBackend`. Scriptet validerer aktiv IPv4-adresse, subnet,
porte, firewall og ejerskab. Det skal køres med de faktiske skoleværdier af
en administrator. Under den aktuelle modning arbejder kodeagenten i kildekode
og isolerede tests; faktisk installation kræver en opgave om den installation.

Den genererede runtime-konfiguration kontrolleres af
[`system/windows-elevhub/server/bootstrap.py`](../system/windows-elevhub/server/bootstrap.py).
Den kræver lokale TLS-filer og register under `security`, validerer
`konfiguration.json`, og starter de autoritative lokale tjenester med
datamapperne `printvaerksted`, `designvaerksted` og `hub`. Et separat LAN
kræver derfor, at adresser og firewallregler stemmer overens i skoleprofilen,
Windows-installationen og serverens konfiguration.

[`system/windows-elevhub/server/hub/konfiguration.windows.json`](../system/windows-elevhub/server/hub/konfiguration.windows.json)
er kilde-skabelonen. Den beskriver blandt andet `backend_ports` og apps med
`port`, `bagved`, `regler` og standardåben. En skolekonfiguration skal holde
værkstedsappens backend på loopback og lade serverens LAN-adresse være den
beskyttede lærer-/Elevhub-adresse. Brug
[`system/windows-elevhub/server/provision.py`](../system/windows-elevhub/server/provision.py)
til enrollmentdata; kopier ikke enrollmentnøgler ind i en generisk pakke.

## Serverindgange og lokale appdescriptors

Serverens faste LAN-indgange beskrives i Linux- og Windows-skabelonerne:
[Linux](../system/linux-elevhub/server/elevhub/konfiguration.json) og
[Windows](../system/windows-elevhub/server/hub/konfiguration.windows.json).
Ved ændring af en serverindgang skal app-id, port, backend og regler
følges ad. Hold backends på loopback og serveradressen som den
beskyttede elev-/lærerindgang.

En ny lokal SDK-app registreres derimod gennem sin installerede
schema-2-descriptor og det signerede appkatalog. Tilføj den ikke til
faste gamle lister over offlinefliser, projektformater eller backends.
Se [ny-app-guiden](NY-APP-GUIDE.md) og [appplatformen](APP-PLATFORM.md).
Lærerens åbning/lukning og installeret softwarebestand er adskilt.

Både Laser og Folie bruger SVG-biblioteket fra den faste
Skoleværkstedet-hjemmeside. Kildevalget er afgrænset i
[svg_library.py](../system/app/svg_library.py); lokal cache kan bruges
uden forbindelse for allerede hentet katalog og figurer. En tom eller
gammel cache vises som sådan; den er ikke en garanti for alle figurer
offline. Biblioteket giver ingen rettighed til at aflevere til køen.

## Maskiner og workflows

Maskiner og workflows skal oprettes gennem lærerens eksisterende
Makerværksted-panel og dets domænefunktioner. Brug
[`makerspace_store.py`](../system/linux-elevhub/server/elevhub/makerspace_store.py) og
[`makerspace_workflows.py`](../system/linux-elevhub/server/elevhub/makerspace_workflows.py)
som kildekodekontrakt sammen med panellets eksisterende kald. En
skoletilpasning skal ikke indsætte maskiner eller workflows direkte i SQLite,
udvide systemet med en ny automatisk driftsklient eller opfinde en parallel kø.

En ny maskine kan registreres uden at være understøttet hardware. En driver,
forbindelse, modelgrænse, fysisk status og adgang til start/frigivelse skal
fortsat håndhæves af den eksisterende adapter. For Bambu-printere går
administrationen gennem [`makerspace_printers.py`](../system/linux-elevhub/server/elevhub/makerspace_printers.py)
til den eksisterende lokale printertjeneste. For laserstationer er
[`makerspace_laser_admin.py`](../system/linux-elevhub/server/elevhub/makerspace_laser_admin.py)
og den autoritative Inbox-kø relevante; registrering eller workflowbinding er
ikke bevis på, at en fysisk laser er online.

Workflows oprettes, valideres, publiceres og tilbageføres i lærerens panel.
Et aktivt job fastholder den publicerede version. En tilpasning må derfor ikke
ændre workflowdefinitioner direkte i en jobrække, slå lærertrin fra eller
fjerne `approve`, `produce`, fysisk kontrol og `clear` fra maskinadapterens
kontroller. Se [arkitekturens workflowafsnit](MAKERSPACE-ARKITEKTUR.md#workflows-der-kan-tilpasses)
for grænsen mellem redigerbare lærerflows og faste sikkerhedsbetingelser.

## Privat konfiguration af projektbibliotekets adresse

Når det lokale projektbibliotek bruger en anden port end 8791, lægges
`print-project-origins.json` i printkomponentens datamappe og
`laser-project-origins.json` i den centrale designkomponents datamappe.
Find de aktuelle mapper gennem installationens komponentvalg eller
tjenesternes `--data`; redigér ikke en låst programgeneration.
Konfigurationen følger komponentens backup og gendannelse.

```json
{"allowed_origins":["http://127.0.0.1:8793","http://localhost:8793"]}
```

Listen erstatter standardlisten. Den må kun indeholde præcise HTTP-loopback-
origins med port; en tom liste slår indlejring fra. Tjenesten afviser ugyldig
konfiguration før binding af HTTP-porten. Lærerflader tillader altid ingen
indlejring. Se [projektbroens kontrakt](MAKERSPACE-ARKITEKTUR.md#print--og-designtjenestens-lokale-projektbro)
for validering og forskellen mellem produktionsindgang og kildeintegration.

## Hvad der er kilde, drift og fremtidig bygning

Filer under `system/`, `apps/`, `sdk/`, `deployment/` og `docs/` er
udviklingskilde. `data/`, lokale skoleprofiler, TLS, enrollment,
bridge-nøgler og SQLite-databaser er driftsdata og må ikke bruges som
skabeloner. Mapper under `output/` og `dist/` er historiske eller verificerede
udgivelser; de skal ikke ændres som led i denne tilpasning.

Kør relevante tests og kildekontroller. Byg de artefakter, opgaven omfatter:
en selvstændig app kræver sin egen pakke, mens en kerne-, Windows- eller
ISO-udgivelse følger den tilsvarende del af
[udgivelsesprocessen](UDGIVELSESPROCES.md). En almindelig kildeændring
udløser ikke automatisk en fuld udgivelse. Gennemfør allerede aftalte
bygge- og testtrin uden en ny generel godkendelsesrunde.

Aflever kildecommit, faktisk kontrol og åbne forhold. Skeln mellem
bygget pakke, testinstallation og drift; LAN-, hardware- og elevudstyrsprøver
skal beskrives som udestående, indtil de er udført på det relevante mål.

## Minimumskontrol for en kodeagent

1. Find den autoritative generator eller skabelon; gæt ikke en skoleprofil.
2. Kontrollér schema, app-id’er, porte, backend og datamapper på begge platforme.
3. Genbrug lærerens panel, versioner, audit og eksisterende adaptere.
4. Hold credentials, elevdata, private stier og skoleværdier ude af kildekode,
   tests og dokumentation.
5. Kør isolerede relevante tests med midlertidige datamapper, lokale
   testtjenester og simuleret hardware; forbind ikke til skolens drift.
6. Meld eksplicit, hvis ændringen kræver fysisk pilot, ny samlet bygning eller
   en fremtidig adapter.

