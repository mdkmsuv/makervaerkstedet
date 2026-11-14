# Programopdateringer via GitHub

Kreditering: Klostermarksskolen og Mathias Kusk Damgaard.

Den faste udgivelseskilde er
[mdkmsuv/makervaerkstedet](https://github.com/mdkmsuv/makervaerkstedet).
Programmet kører lokalt på skolens computere. Daglig drift kræver hverken
AI, Codex, GitHub-login eller den oprindelige udviklingsmappe. Nye
udgivelser kræver netværk til GitHub; allerede verificerede lokale
pakker og installerede apps kan bruges uden den forbindelse.

## Aktuel Linux-vej

Linux-elevenheder bruger native apt. Skolens Windows-server importerer
et signeret snapshot; elevcomputeren henter fra skolens autentificerede
HTTPS/CA og kontrollerer også apt-repositoryets GPG-signatur.

| Trin | Ansvar og bevis |
|---|---|
| Byg | Rene, dokumenterede kilder, låste inputs, native pakker og præcis installationssimulation |
| Signér | GPG for apt; minisign for feed, coremanifest og separat appkatalog |
| Publicér | Versioneret GitHub-release efter konkret mandat |
| Importér | Serverens `simple_update_import.py` verificerer feed, snapshot, katalog og hashes før lokal publicering |
| Bestil | Læreren vælger core under **Teknik** eller apps under **Appkatalog** samt konkrete computere |
| Udfør | Den installerede native updater verificerer og installerer den bestilte, præcise ændring |
| Bekræft | Faktisk native rapport, activation og relevant tilstand; download eller ordre er ikke færdig installation |

De faste feed-assets er `makervaerkstedet-updates.json`,
`makervaerkstedet-updates.json.minisig` og
`makervaerkstedet-apt-repository.zip`. De fremstilles af
`scripts/build_simple_feed.py` fra et allerede signeret repository
og en kontrolleret producentkvittering. GitHubs automatiske source-arkiv
er ikke en installationspakke.

Core bruger fortsat den eksisterende schema-1-kanal
`/api/device/updates`; modulære apps bruger et separat schema-2-katalog
og `/api/device/apps`. Disse protokoller må ikke blandes.
Se [apt-kontrakten](APT-REPOSITORY.md) og
[appplatformen](APP-PLATFORM.md).

## Selvstændige apps

En ændring i `apps/<id>/` har egen version, pakke og artefakt.
Et nyt reviewet appbyg kræver ingen genbygning eller versionsændring
af hovedsystemet. Den færdige pakke skal passe til katalogets descriptor,
kildecommit, hash og sekvens og eksisterende SDK-/kernekrav.
Samme version må ikke genudgives med andre bytes.

Læreren vælger **Installér**, **Opdatér** eller **Afinstallér** i
**Appkatalog**. Installeret og tilgængelig vises adskilt med den
rapporterede tilstand pr. computer. Offline betyder afventende kontakt,
og ukendt udfald afklares med samme bestillings-id.
Gemte projekter og ventende afleveringer bevares ved afinstallation.

Skolens appstandard gælder nye tilmeldte enheder. **Apps og internet**
åbner eller lukker undervisningsadgang; det installerer og fjerner
ingen software. Eksisterende enheder på den gamle samlede kerne
migreres med bevarelse af deres appbestand, efter bro-opdatereren er
observeret. Se [udgivelsesprocessen](UDGIVELSESPROCES.md).

## Drift uden udviklingsmappen

En genskabelig udgivelse kræver et fast kildecommit eller en verificeret
offentlig eksport med `REPOSITORY-LAYOUT.json`, låste browserbyg og
licensfiler, samt gennemgåede eksterne inputs med version og SHA256.
Windows-runtime, Scratch og Matchbox må ikke afhænge af en løs sti til
en udviklers installation. Store inputs kan leveres som kontrollerede
release-assets.

Programfiler ligger i installationens egne mapper og runtimes.
Skoleprofil, logo, skrifttypevalg, konfiguration, certifikater, nøgler,
maskiner, elevprojekter, køer, workflows og historik er lokale data.
De bevares ved opdatering og følger skolens backup. En download
genskaber programfiler, aldrig skolens data.

Den særskilte historiske engangsovergang fra før 2.0 har andre regler
for gamle elevprojekter; det er ikke den modulære migration og må ikke
køres igen for at opdele apps. Se [backup](MAKERSPACE-BACKUP.md) og
[skoletilpasning](SKOLETILPASNING.md).

## Windows og historisk ZIP-protokol

Windows-server og laserstation bruger deres dokumenterede rolle-EXE
og installations-/opdateringsadaptere. Følg
[EXE/ISO-guiden](INSTALLATION-FRA-EXE-OG-ISO.md) og leverancens præcise
README. En Linux-appordre er ikke en Windows-serveropdatering.

De ældre `makervaerkstedet-update.json`, `linux-queue-v1`,
`laser-code-v1`, `school-server-v1` og
`scripts/build_github_release.py` beskriver den tidligere ZIP-baserede
rollevej. Filer og workflows kan stadig findes som historisk integration.
Brug dem ikke som opskrift på modulære Linux-apps, og antag ikke at et
eksisterende GitHub-workflow automatisk udgiver det nye native sæt.

Et commit, et lokalt byg eller en draft release er ikke en publiceret
installationsudgivelse. Kildeversion, bygget kandidat, signatur,
testinstallation, ISO-boot og drift skal hver dokumenteres særskilt i
[udgivelsesprocessen](UDGIVELSESPROCES.md).
