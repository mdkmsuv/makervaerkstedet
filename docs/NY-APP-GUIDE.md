# Sådan udvikler og opdaterer du en app til Makerværkstedet

Aktuel kildeopdeling, 16. november 2026. Kommandoerne køres fra produktroden,
som indeholder `REPOSITORY-LAYOUT.json`. `system/` ejer hovedsystemet,
`apps/<id>/` ejer appens kode og pakkemanifester, og `sdk/` indeholder
udviklingsskabeloner. Installerede Linux-stier er uændrede. Se også
[appplatformen](APP-PLATFORM.md) og [udgivelsesprocessen](UDGIVELSESPROCES.md).

En ny app kræver ingen registrering i faste Python-lister, ændring af
hovedsystemets version eller genbygning af hovedsystemets pakke. Dens
schema-2-manifest bliver først betroet på en elevenhed gennem det signerede
katalog og den eksisterende opdateringstjeneste. En lokalt bygget `.deb`
er en selvstændig byggeprøve; den er ikke en godkendt offentlig udgivelse.

## Opdater en eksisterende app

Start her og i den konkrete `apps/<id>/README.md`. Du skal kunne følge
appens bygge-/testopskrift derfra; hovedsystemets implementering er ikke
en forudsætning for at ændre en app.

1. Læs appens `app.json`, `files.json` og README. Følg appens egne byggenoter,
   hvis den har en editor eller bundling; Scratch har eksempelvis
   [sin egen opskrift](../apps/scratch/build/README.md).
2. Ret appens egne filer og bump kun dens `version`. Bevar id, pakke, port,
   projektformat og eksisterende gemning. Skift kun `min_core`, hvis den nye
   funktion faktisk kræver en nyere kontrakt.
3. Kør appens dokumenterede prøver og byg. Kontroller licenser og at nødvendigt
   indhold følger med til offline brug. Prøv den renderede brugerhandling.
4. Byg den ene apppakke som beskrevet i afsnit 6 og appens opskrift. En
   statisk app behøver ingen kernesources eller nyt kernebyg. Scratch har et
   særskilt editorbyg og en inputlås, som appens egen pakkeopskrift fremstiller.
5. Brug udgivelsesvejen i afsnit 7 med den nye apppakke og **uændrede input
   for det øvrige aktuelle udvalg**. Kataloget beskriver hele udvalget,
   selv når kun én appversion ændres. Genbyg ikke de øvrige pakker.
6. Bestil **Opdatér** på den aftalte testelevenhed som i afsnit 8. Kontroller
   den faktisk installerede version og det nye indhold, bevarede projekter
   og uændrede øvrige apps. Et byg eller et grønt bestillingssvar er ikke
   i sig selv bevis for installation.
7. Ajourfør appens README med den nye funktion og prøvekommandoer. Registrer
   kilde, byg, faktisk installation, åbne forhold og næste arbejde i
   projektets fælles status. Bevar relevante kvitteringer; ryd egne prøver.

Som eksempel tilføjer Scratch `15.1.1+local.20261116.3` lokale CC0-figurer
udelukkende gennem `apps/scratch/`. Den ændring kræver ingen ændring eller
ny version af hovedsystemet. Privat testdistribution og offentlig publicering
er fortsat forskellige handlinger med hver sit mandat.

## 1. Opret og prøv den mindste app

Python 3.13 og Node er udviklingsværktøjer. De statiske skabeloner behøver
hverken npm-installation, bundling, netværk eller nye konti. Vælg et nyt id
med små bogstaver, tal og bindestreg og en ledig loopback-port over 1023.
Port 8790 og 8791 er systemporte; eksisterende apps bruger også egne porte.

```powershell
python -B sdk/new_app.py --id plantequiz --name "Plantequiz" --port 8890 --stateless
python -B sdk/check_app.py --app plantequiz
python -B sdk/preview.py --app plantequiz
```

Uden `--stateless` eller `--project` vælges samme quiz uden lagring.
Åbn den udskrevne lokale adresse. Quizzen har et spørgsmål, kan betjenes
med tastatur og viser både manglende, forkert og korrekt svar. Den
indsætter ingen projektadapter eller gemmedialog. Svar gemmes ikke.

For en app med gem og genåbn:

```powershell
python -B sdk/new_app.py --id idebog --name "Min idébog" --port 8891 --project
python -B sdk/check_app.py --app idebog
python -B sdk/preview.py --app idebog
```

Opretteren skriver kun den nye mappe `apps/<id>/` og afviser eksisterende
mapper, links/junctions, ugyldige identiteter og portkonflikter. Den ændrer
ikke systemkode, rodfilernes buildkommandoer eller andre apps.

`preview.py` pakker appens filer til en midlertidig mappe, kontrollerer den
med den rigtige pakkekontrakt og bruger den eksisterende lokale vært,
temabro og projektbro. Kun loopback-porten i manifestet åbnes. Projektdata
fra prøven er midlertidige og fjernes ved Ctrl+C. Prøven starter ingen kø,
opdateringstjeneste eller installation og afviser apps med køcapabilities.
Den erstatter ikke en installeret prøve med elevbjælke, offline genstart
og lærerens adgangsstyring.

Preview bruger en frossen kopi af appen. Stop med Ctrl+C og start igen efter
kildeændringer; der er ingen automatisk genindlæsning. Den lille
[lommeregner](../apps/lommeregner/README.md) er et eksempel på at erstatte
quizindholdet med en anden stateless app uden at ændre hovedsystemet.

## 2. Appens egne filer og kontrakt

Opretteren giver følgende filer:

| Fil | Ansvar |
|---|---|
| `apps/<id>/app.json` | Deklarativ identitet, version, port, SDK, projektformat og capabilities |
| `apps/<id>/files.json` | Præcis kilde- og pakkeejerskabsliste |
| `apps/<id>/web/index.html`, `style.css`, `app.js` | Færdig, lokal brugerflade |
| `apps/<id>/web/notes-adapter.mjs` | Kun i projektvarianten: typet notesformat og adapter |
| `apps/<id>/README.md` | Appens lokale kontrol og prøvekommandoer |

Manifestet valideres af `system/app/app_catalog.py`, ikke en kopi af
reglerne i SDK'et. Pakken hedder `makervaerkstedet-app-<id>`. Projektets
`kind` er samme id; filendelsen afledes af id'et, uden bindestreger og med
`.mkv` til sidst. Den faktisk valgte endelse står i manifestet. Kollisioner
og reserverede formater afvises. Tilføj ikke en ny projekttype til gamle
faste lister: den installerede descriptor registrerer formatet dynamisk.

`files.json` læser kun appens egen kildemappe. Pakkens programfiler må kun
ligge under `usr/share/makervaerkstedet/apps/<id>/`. Der følger ingen
Python-hooks, shellkommandoer, systemd-filer, profiler, nøgler eller
elevdata med. Kernen importerer ikke appkode.

Bump kun appens `version`, når den ændres. Hold `sdk`, `min_core` og
capabilities i overensstemmelse med den faktisk brugte kontrakt. Den
aktuelle SDK-version er 1; nye apps kræver mindst core `2.1.0~`.
Den offentlige katalogvalidering kontrollerer også pakkeidentitet,
portkonflikter, projektformater, artefakthash og kildecommit.

## 3. Tema, logo, skrifttype og navigation

`theme-v1` i skabelonens optional capabilities får værten til at indsætte
`/school-theme.css` og `/school-theme.js`. Værten leverer også
`/assets/metrophobic-latin.ttf`. Brug `--brand` og `--brand-dark`, og behold
`data-school-name`, `.brand` og `.brand-mark`, så skolens validerede lokale
profil kan sætte navn og logo. Medtag ingen skolespecifik profil i appen.
Det fælles skoletema kan indlæse den godkendte lokale profiltypografi.
Appens offentlige standard er Metrophobic.

Brug almindelig browsernavigation og elevbjælkens «Til start».
Et sideskift i projektvarianten går gennem den fælles afslutningsdialog.
Brug ikke `location.replace` til at omgå den og opret ikke egne elevkonti.
Sidens titel må ikke begynde med `Makerværkstedet · Værksteder`, som
identificerer startsiden. Den synlige projektkreditering bevares uafhængigt
af skolens navn og logo.

## 4. Gem, genåbn og elevskift

For en descriptor med `project` indsætter værten automatisk
`/_elevhub/projekter/bridge.js`, som bruger den faste projekt- og
sessionbro. Den venter på `window.ElevhubProjectAdapter`. Skabelonens
notesadapter viser den fulde kontrakt:

- `type`, `extension`, `name()`, `export()` → Blob,
  `import(blob, name)` og `reset()`.
- `validateImport(blob)` afviser fremmede, beskadigede og for store filer,
  før åbent arbejde ændres.
- `signature()` er stabil for uændret indhold, `hasContent()` opdager
  eksisterende arbejde, og `setName()` følger det fælles projektnavn.

Appen validerer sine egne opaque bytes: noter har schema, app-id og tekst.
Systemet opbevarer og versionerer filerne uden at eksekvere dem. Der er
ingen automatisk genåbning af den forrige elevs dokument. `reset()` tømmer
både indhold og navn. Gem ikke elevindhold i localStorage eller cookies.

Projektbroen ejer klasse/navn/projektnavn, gem/åbn, gendannelse og
`/_elevhub/session/poll`/`ack`. Send ikke `elevhub-project-changed` ved
hvert tastetryk: det ophæver den eksisterende projektbinding. Brug eventet,
når appen selv erstatter projektet uden om broen. Skabelonen behøver det
ikke under almindelig indtastning.

**Autogem er appens ansvar.** Læg appens ændringsregistrering, tidsstyring og
gemmestatus i `apps/<id>/`; for Scratch i `apps/scratch/`. Appen kan bruge
`window.ElevhubProjectSession.save()` til den eksisterende lokale gemmefunktion,
efter at eleven har navngivet projektet. Selve skrivningen og versioneringen
må fortsat bruge fælles lokal infrastruktur på elevcomputeren. Skolens server
skal ikke have autogem-logik eller løbende kopier af elevens projekt. Autogem
skal virke uden forbindelse til skoleserveren og følge det eksisterende
elevskift, så næste elev ikke fortsætter i forrige elevs projekt.

En app, der vælger autogem, skal kræve mindst core `2.1.0~20261116.1` i
manifestet. `saveState()` fortæller, om sessionen er klar og allerede har en
bekræftet arkivfil. Gem også første navngivne projekt, når `stored` er falsk.
Bevar referencen fra `naming()` under en asynkron ændringskontrol, og kald
`save({background:true,expectedNaming})`. `null` betyder afvist kontekstskift,
`false` betyder faktisk gemmefejl, og `true` er en kvitteret gemning. Vis ikke
Gemt, hvis projektet er ændret igen under skrivningen. Adapterens `prepareSave`
og `export` får `{background:true}` og skal undgå at afbryde aktiv redigering.
`saveState().historical` angiver en tidligere åbnet version: autogem skal være
på pause og vise dette, indtil eleven selv gemmer eller laver en kopi. En
historisk åbning må aldrig automatisk erstatte den nyeste arbejdsudgave.

Scratch og 3D har denne app-ejede integration i `web/autosave.js`. Autogem
bevarer Fortryd/Gentag i den åbne editor og erstatter kun den forrige
automatiske arbejdsudgave ved gentagne autogemninger; manuelle versioner
bevares. Gendan-kopien i browseren er en særskilt sikkerhedsfunktion.
Se [ansvarsgrænse og versionskrav](APP-PLATFORM.md#autogem-tilhører-appen-og-virker-lokalt).

Appens grænse er 1 MiB og 100.000 tegn i noteseksemplet; øg kun inden for
systemets grænse på 50 MiB. Ugyldig import skal bevare det åbne dokument.
Prøv også afvisning, gemmefejl, genåbning, elevskift og gendannelse.

## 5. Valgfri aflevering til kø

`sdk/examples/queue.mjs` er et tilvalgseksempel og udfører intet ved
indlæsning. Kopiér det ind i appens egen `web/`-mappe og deklarér den
konkrete optional/required capability i `app.json`:

| Capability | Fast lokal route | Data |
|---|---|---|
| `laser-submit-v1` | `POST /api/laser/submit` | JSON med `elevnavn`, `klasse`, `projekt`, `svg`, `requestId` |
| `print-job-v1` | `POST /api/jobs` | Binær STL; `X-Filename` og `X-Request-ID` |

Hvis `aflevering` sættes, er værdien kun `laser-submit` eller `print-job`
og skal have den tilsvarende capability. Værten vælger den eksisterende
lokale gateway. Appen får ingen vilkårlig serveradresse, shelladgang eller
nye endpoints. Indholdet valideres igen af gatewayen.

Opret ét `crypto.randomUUID()` ved elevens konkrete aflevering, og genbrug
samme id ved afklaring/genforsøg af samme aflevering. Automatisk gentagelse
med nye id'er kan skabe dubletter. Netværksfejl eller ukendt kvittering
betyder ukendt udfald; behold arbejdet og bed om afklaring.

Eksemplet skelner mellem `202` med `queued: true` («Gemt i udbakken.
Venter på forbindelse»), en `200`/`201` med kø-id («Modtaget i køen») og
lærerens efterfølgende fysiske arbejde. Det kalder aldrig en kvittering
for fysisk færdig. `423` vises som lærerens lukning; afvisning bevarer
elevens arbejde. Prøv kun med mocks eller aftalt testmiljø. En reel
laser-/printaflevering er ikke en harmløs SDK-test.

## 6. Kontrol og selvstændigt byg

Fra produktroden:

```powershell
python -B -m unittest discover -s tests -p test_sdk_template.py -v
python -B -m unittest discover -s tests -p test_app_catalog.py -v
python -B -m unittest discover -s tests -p test_app_package_ownership.py -v
python -B sdk/check_app.py --app idebog
python -B scripts/public_source.py --check
```

SDK-testen opretter kun midlertidige apps. Den prøver pakkeejerskab,
link-/overskrivningsafvisning, opaque import/eksport/reset, rigtige lokale
HTTP-routes og tema/font/projektbro samt køeksemplets kvitteringer med
mocks. Den tilføjer ingen sjette indbygget app.

De to statiske skabeloner er klar til pakning uden bundling. Eksisterende
apps med bundling bygger fra deres egne mapper:

```powershell
npm run build:laser
npm run build:model3d
npm run build
# En kildeeksport kan genbruge en eksisterende fastlåst node_modules:
npm run build:model3d -- --dependencies C:/sti/til/node_modules
npm run build -- --dependencies C:/sti/til/node_modules
```

`build:laser` delegerer til `apps/folie/build.mjs`, `build:model3d` til
`apps/3dprint/build.mjs`, mens `build` bygger systemets viewer og Folie.
3D-bygget har kilde-/font-/licenskvittering, som pakkegaten kontrollerer.
`package-lock.json` skal bevares. Scratch har sit eget dokumenterede byg
under `apps/scratch/build/`.

For en statisk skabelon fremstilles én apppakke på Debian 13 amd64/WSL.
Her er `out/app-idebog` en ny, lokal outputmappe:

```bash
python -B system/linux-elevhub/packaging/prepare_app_inputs.py \
  --source-root "$PWD" --app idebog --output-dir out/app-idebog
python -B system/linux-elevhub/packaging/build_debian.py \
  --input-lock out/app-idebog/makervaerkstedet-app-idebog.json \
  --output out/app-idebog/makervaerkstedet-app-idebog_1.0.0_all.deb
```

Ved kildeudgivelse skal den reviewede `apps/<id>/README.md` også tilføjes
til `explicitFiles` i `deployment/public-source-manifest.json`, ligesom
projektets øvrige offentlige vejledninger. Programfilerne under `apps/`
medtages allerede; dokumenter kræver et udtrykkeligt valg. Kontrollen skal
bestå, før en offentlig eksport kan indeholde links til appens vejledning.

Det bygger ingen hovedsystempakke og installerer intet. Inputlock og
`.inputs.json` er byggekvitteringer, ikke skolekonfiguration. En appændring
må ikke ændre hovedsystemets kildehash eller version. Ved 3D-pakning fra
frossen kilde kan `prepare_app_inputs.py --vendor-built-from /sti/til/byg`
pege på de faktiske kilder, som kvitteringen blev bygget fra.

## 7. Signeret katalog og distribution

Kør kun efter review og konkret udgivelsesmandat. Brug et fastlagt,
committet kildecommit, uændrede tillidsankre og eksisterende signer.
Ingen appudvikler behøver at oprette eller eksportere private nøgler.

`build_app_catalog.py` tager følgende præcise inputstruktur. Størrelse og
SHA256 tages fra den faktisk byggede `.deb`; descriptoren læses fra
`apps/idebog/app.json`. `source_commit` er 40 små Git-hextegn fra den
reviewede kilde. Version, sequence og release-tag vælges til udgivelsen;
sequence må ikke genbruges eller rulles tilbage.

```json
{
  "schema": 2,
  "version": "1.0.0",
  "sequence": 1,
  "source_commit": "<40 hextegn fra reviewet commit>",
  "release_tag": "apps-1.0.0",
  "packages": [{"path": "/absolut/sti/makervaerkstedet-app-idebog_1.0.0_all.deb",
                "size": 1234, "sha256": "<64 hextegn>",
                "package": "makervaerkstedet-app-idebog", "version": "1.0.0", "architecture": "all"}],
  "app_descriptors": ["<erstat med hele objektet fra app.json>"]
}
```

Pladsholderne er dokumentation, ikke gyldige input. Katalogproducenten
kontrollerer de faktiske native pakkefelter og den pakkede descriptor:

```bash
python -B scripts/build_app_catalog.py \
  --inputs /privat/byg/catalog-inputs.json --output /privat/byg/catalog.json
```

Det resultat er endnu usigneret. Den eksisterende udgivelsesvej samler
basis + en eller flere apps (`--release-set app`) eller det samlede migrationssæt og
kører isoleret apt-simulering med de præcise pakker:

```bash
python -B system/linux-elevhub/packaging/build_release.py \
  --release-set app --input-locks /privat/byg/elev.json /privat/byg/updater.json \
  /privat/byg/matchbox.json /privat/byg/idebog.json \
  --output-dir /privat/byg/release --report-dir /privat/byg/reports \
  --debian-keyring /sti/til/verificeret/debian-archive-keyring.gpg
```

Et katalog beskriver hele det aktuelle udvalg. For at **tilføje** en app og
bevare de eksisterende valg skal deres input-locks og descriptors også med i
udgivelsen; ellers er det et nyt udvalg med kun den ene app. Tilføj de øvrige
app-locks efter `--input-locks`, og brug samme fulde appudvalg i katalog- og
apt-inputtet. Allerede byggede pakker kan genbruges i outputmappen, når deres
kvitteringer og hashes matcher. En ny app kræver dermed ikke en ny version
eller genbygning af kernen. Gaten kontrollerer stadig alle pakkeidentiteter,
fil-ejerskab og den rigtige isolerede apt-simulering; vilkårlige pakker tillades
ikke. `migration` er fortsat det faste sæt til de fem oprindelige apps.

GPG-signeret apt-repository fremstilles med `scripts/build_apt_repository.py`
(`--inputs`, `--output`, `--gpg-exe`, `--gpg-sha256`, `--gnupg-home`,
`--key-fingerprint`, `--receipt`). Inputschema 2 skal binde samme release-set,
app-descriptors, pakkehashes og den beståede isolerede installationsgate.
Brug de eksisterende signer-/receipt-værktøjer beskrevet i
[udgivelsesprocessen](UDGIVELSESPROCES.md); udfyld ikke kvitteringer i hånden.

Fra Windows bruges det eksisterende DPAPI-beskyttede signer-materiale til
feed og minisign-katalog. Alle parametre er faktiske stier/hashes fra den
kontrollerede udgivelse, og outputmapper skal være nye:

```powershell
python -B scripts/build_simple_feed.py --repo C:/byg/signeret-apt --output C:/byg/feed `
  --receipt C:/privat/byg/apt-receipt.json --app-catalog C:/privat/byg/catalog.json `
  --app-catalog-sha256 <katalogets-SHA256> --minisign-exe C:/vaerktoej/minisign.exe `
  --minisign-sha256 <vaerktoejs-SHA256> --public-key <eksisterende-offentlig-noegle> `
  --release-tag apps-1.0.0 --sequence <naeste-sequence> --result C:/privat/byg/feed-result.json
```

Kommandoen kontrollerer apt-receipt, pakke-/katalogbinding og eksisterende
signaturankre; den publicerer ikke. Publicering til det faste GitHub-feed og
installation på skolens drift kræver deres konkrete bestilling. Ændr ikke
repoidentitet, updaterens offentlige nøgle eller tillidsanker for at få en
lokal app accepteret.

## 8. Lærerens installation, opdatering og fjernelse

I lærerens kontrolpanel vælges **Appkatalog**, en godkendt app, de konkrete
elevcomputere og **Installér**, **Opdatér** eller **Afinstallér**. Bekræft
bestillingen. En bestilling er ikke en bekræftet installation; følg
computernes rapporterede tilstand. Gemte projekter bevares ved fjernelse,
og et offline apparat venter på forbindelse. Standardsæt gælder nye
tilmeldte elevenheder, ikke en tavs masseinstallation på eksisterende.

Afinstallation fjerner selve apppakken og programfilerne fra elevenheden.
Updateren fjerner også appens gamle og aktuelle `.deb`-arkiver fra apt-cachen,
så installation/opdatering/fjernelse ikke ophober kopier af den app. Andre
pakkers cache og elevens projekter berøres ikke. Små katalogmetadata og
handlingskvitteringer bevares til status og sikker genoptagelse.

Afprøv hele forløbet **installation → afinstallation → installation igen**
på den aftalte testelevenhed. Gem pakkens faktiske filliste fra
`dpkg-query -L <pakkenavn>` før fjernelse, og kontrollér bagefter, at alle
dens programfiler og programmapper er væk, at appens port er lukket, og at
dens `.deb`-arkiver er ryddet. Kontrollér også bevarede projekter, skoleprofil,
enhedsidentitet og øvrige apps. Efter geninstallation skal samme godkendte
version starte igen. Et grønt serverjob eller et manglende `app.json` alene
beviser ikke, at alle programfiler er fjernet; en app kan eje flere programrødder.

Den faktiske HTTP-kontrakt er `GET /api/admin/apps` og
`POST /api/admin/apps/order` med lærerlogin, samme Origin og disse felter:

```json
{"command_id":"<nyt UUID>","expected_revision":12,"devices":["<enheds-id>"],
 "app_id":"idebog","action":"install","catalog_sha256":"<godkendt kataloghash>"}
```

Brug `update` eller `remove` for de andre handlinger. Revision, enheder og
kataloghash hentes fra den aktuelle GET-status. Genbrug samme command-id
og nøjagtige payload ved **Afklar samme bestilling**; lav ikke blindt en
ny efter ukendt udfald. Ved 409 hentes frisk status før en ny beslutning.
`POST /api/admin/apps/defaults` bruger `command_id`, `expected_revision`,
`catalog_sha256` og listen `apps`. Kataloget accepterer ingen vilkårlig
apt-pakke eller URL, og elevbrowseren kan ikke udføre lærerordrer.

## Færdigkontrol

Prøv den faktisk renderede app ved 1024 × 688 og 1024 × 768, også med
tastatur og relevant zoom. Målgruppen skal kunne forstå første handling,
fejl og næste skridt. Følg [UI/UX-kravene](UI-UX-KRAV.md), synligt fokus,
44 × 44 px klikmål og dansk klarsprog; få et uafhængigt review efter
[UI/UX-rollekortet](agenter/UI-UX-AGENT.md).

Afprøv projektvarianten fra navngivning til gem, afslutning, genåbning og
ny elev med tomme felter. Prøv installeret offline/kold opstart og fejl,
uden at starte fysisk maskinarbejde. Kontroller licenser og kreditering,
offentlig kildeallowlist og at ingen skoledata er kommet med. Aflever med
præcis skelnen mellem kilde, lokal byggeprøve, signeret udgivelse,
testinstallation og drift. En plan eller grøn kildetest erstatter ingen af dem.
