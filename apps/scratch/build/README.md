# Lokal Scratch-editor

Dette er integrationsnoter for en statisk Scratch 3-editor, som elevcomputerens
lokale tjeneste serverer på `http://127.0.0.1:8792/`. Konfigurationen bruger
`type: "lokal"` og `lokal_service: true`; den centrale server hoster ikke editoren
eller projekterne. Kildekode, `node_modules` og udviklingsbygget ligger med
vilje uden for repositoryt i `<LOKAL-TESTMAPPE>\scratch`.

## Fast upstream-version og output

- upstream: <https://github.com/scratchfoundation/scratch-editor>
- tag: `v15.1.1`
- commit: `99bcc17e0580588f181f8a87577a2f676537a487`
- aktuelt byg-output: `<LOKAL-TESTMAPPE>\scratch\scratch-gui-v15.1.1-local-projects-build`
- lokal rod: `index.html` (produktionsbygget standalone-editor)
- alternativ rod: `standalone.html`
- produktionsbundle: `guistandalone.js` (standalone playground entry)
- corresponding source: `source.zip` (SHA-256 registreret i `installation.json`)

`scratch-gui` ligger nu i dette monorepo; det tidligere selvstændige repository
er arkiveret. `installation.json` i outputmappen registrerer den faktiske kilde,
pin, outputsti og integrationstilpasning. Licens- og varemærkefilerne
`LICENSE`, `LICENSE-scratch-gui`, `TRADEMARK` og `TRADEMARK-scratch-gui` følger
med i outputmappen.
Upstream Scratch-koden er AGPL-3.0; ElevHub-integrationen er krediteret til
Mathias Kusk Damgaard. Editorens synlige bjælke linker til `source.zip` og
licenserne. Arkivet indeholder den patchede, git-trackede kilde, root
`package-lock.json`, buildscripts og integrationspatchen, men ikke `.git`,
`node_modules` eller byg-output.

Den fulde kildepakke er i denne pin cirka 83,5 MB (under 128 MiB); den lokale
`source.zip`-rute skal derfor have en særskilt større grænse end øvrige filer.

## Reproducerbart build til Linux-værkstedet på en Windows-vært

Kør fra produktroden i PowerShell. Python, Node/npm, Git og PowerShell 7 er
udviklingsværktøjer. Den fastlåste Pillow-version bruges kun under byg til
at opdele Pixel Frogs PNG-animationsark; den installeres ikke på elevenheden.
Scratch-integrationen ejes af `apps/scratch/`:

```powershell
python -m pip install -r apps/scratch/build/requirements-library.txt
python -Xutf8 -B apps/scratch/build/fetch_library.py
python -Xutf8 -B apps/scratch/build/test_extra_library.py
python -Xutf8 -B apps/scratch/build/test_editor_package.py
$scratchRoot = Join-Path $env:TEMP 'mkv-scratch-candidate'
pwsh -File .\apps\scratch\build\build-scratch.ps1 -ScratchRoot $scratchRoot
pwsh -File .\apps\scratch\build\smoke-test.ps1 -BuildPath (Join-Path $scratchRoot 'scratch-gui-v15.1.1-build')
```

Scriptet afviser eksisterende source- og outputmapper for at undgå overskrivning.
Vælg derfor en ny `ScratchRoot` til et nyt byg. Standardroden ligger i brugerens
midlertidige mappe; en gammel D:-arbejdskopi er ikke nødvendig. Metadata,
editor og `source.zip` ligger alle i `scratch-gui-v15.1.1-build/` under den rod.
En ny kildesamling kan dermed følge opskriften uden lokale filer fra tidligere
leverancer. Arkivhenteren kontrollerer størrelse/SHA256 før brug; ændrede eller
manglende udgivelser giver fejl, ikke en tavs opdatering af billedindholdet.

Ved en efterfølgende **ren biblioteksændring** kan det samme fuldførte,
fastlåste checkout genbruges med en ny outputmappe:

```powershell
pwsh -File .\apps\scratch\build\build-scratch.ps1 -ScratchRoot $scratchRoot `
  -ReusePreparedSource -OutputName 'scratch-gui-v15.1.1-library-update'
```

Det kontrollerer den oprindelige bygkvittering, upstream-commit og source.zip,
genbygger elevens editor og laver en ny kildepakke. Det genbruger eksisterende
Node-afhængigheder og integrationspatch. Brug et nyt fuldt byg ved ændring af
afhængigheder, adapter eller øvrig upstream-integration. Eksisterende output
overskrives aldrig.
Det kloner den faste tag, kontrollerer committen, kører `npm ci --ignore-scripts`,
bygger de nødvendige workspace-pakker og kontrollerer hvert git/npm/node/npx-
kalds exitkode. Selve skole-entrypointet bygges med
`NODE_ENV=production BUILD_TYPE=dev`, fordi upstreams `dev`-webpack-target
indeholder den faktiske `src/playground/standalone.jsx`-renderer; det er ikke
den almindelige `index.html`-playground. `dist`-targetene bygges også for at
kontrollere den pinned shipping library.

Før GUI-buildet laver scriptet dokumenterede lokale integrationstilpasninger i
den eksterne checkout (`legacy-library-asset-url.ts`, `legacy-storage.ts` og
`render-gui-standalone.jsx`). De peger standardbibliotekets asset-URL'er på den
lokale assetpakke; asset-fetch får en absolut URL, så scratch-storage's worker
kan hente dem. Buildet starter en rigtig standalone-editor uden
backpack/community/cloud-kontroller.
`local-project-adapter.js` kobler editorens VM til den lokale projekthylde.
Eksport gemmer hele `.sb3`, og import bruger VM'ens projektindlæsning.
Nyt projekt og filimport bryder bindingen til et tidligere gemt projekt.
Projektbroen leveres af [den lokale tjeneste](../../../system/linux-elevhub/projekter/README.md).
Den ændrer ikke upstream-pin eller filer i dette repositorys færdige editor.

**Autogem fra app `15.1.1+local.20261116.1` / core `2.1.0~20261116.1`:**
`../web/creative-app.js` indlæser appens egen `../web/autosave.js`.
Den gemmer første navngivne projekt og kontrollerer derefter ændringer cirka
hvert femte sekund. Hele `.sb3` gemmes på elevcomputeren. Eleven venter på
**Gemt på denne computer** og genåbner gennem **Find mit projekt** på samme enhed.
Skoleserver og netforbindelse er ikke nødvendige. En afbrudt gemning kan kun
gendannes frem til seneste færdige gemmekvittering, ikke tastetryk efter den.

Autogem eksporterer indholdet uden at genindlæse det eller nulstille Fortryd/
Gentag. Normal manuel **Gem projekt** er fortsat tilgængelig og bevarer en
manuel version; gentagne automatiske gemninger erstatter den forrige auto-
arbejdsudgave. Browserens særskilte 15-sekunders gendannelseskopi bevares også.
Ved åbning af en tidligere version viser appen, at autogem er på pause; eleven
vælger selv **Gem projekt** eller **Gem kopi**, før der gemmes videre.
Appsiden ejer timer og status; fælles lokal projekthylde og baggrundsgemmegate
sørger for sikker skrivning og elevskift. Se
[appens ansvarsgrænse](../../../docs/APP-PLATFORM.md#autogem-tilhører-appen-og-virker-lokalt).

`dependency.patch` låser desuden hull.js til den rettede Git-commit
`9de6f9549b74fbb68bf4d5a449147b4c1d7cda0a` (version 1.0.10) og bevarer
Scratchs adfærd for små og tomme figurgrænser. Patchen omfatter package-/
lockfilerne og RenderWebGL og anvendes før `npm ci`.

Efter rettelsen giver `npm audit --omit=dev` 37 fund: 3 low, 14 moderate,
14 high og 6 critical. De resterende critical-pakker blev ikke fundet i den
faktiske browserpakke; de bruges af Node-/buildværktøjerne. Der er stadig
uafklarede fund. Kontrollér afhængighederne særskilt før en lokal installation;
advisories, kildekontrol og kompatibilitetsprøven.

## Lokale medier og offline-drift

### Ekstra figurer fra appversion 15.1.1+local.20261116.3

Alt ekstra byggemateriale ejes af denne app:

| Fil | Formål |
|---|---|
| `../library/sources.json` | Ophav, CC0-licens, uændrede arkivers størrelser og SHA256 samt figurnavne |
| `fetch_library.py` og `../archive-cache/*.zip` | Henter og verificerer de originale CC0-arkiver fra ophavsmændene; cache er privat byggemateriale |
| `../library/CC0-NOTICE.txt` | Kreditering og licensafgrænsning |
| `extra_library.py` | Omdanner arkiverne til native Scratch-biblioteksposter og PNG-kostumer |
| `test_extra_library.py` | Rigtige billedceller, animationsrækkefølge, bevaring af standardfigurer, reproducerbare bytes og afvisning af ændret kilde |

De seks Kenney-figurer har hver 45 positurer. Fire Pixel Frog-figurer har
hver 43 animationsbilleder; hvert 32 × 32-billede skæres ud af sit animationsark
uden ændring af pixels. Pixel-figurer starter i størrelse 300 %, så de er
lette at se. Rotationens centrum er ens gennem hver series kostumer.
Der tilføjes ingen automatisk kode til elevens projekt.

Bygget udvider **den almindelige figur- og kostumevælger** og bevarer de
officielle standardfigurer. Søg efter `Kenney` eller `Pixel`. Kostumer er
ordnet pr. handling og nummereret, eksempelvis `gå-01` … `gå-08` eller
`løb-01` … `løb-12`. `extra-library-manifest.json` i outputtet angiver
det faktisk genererede antal og pladsforbrug. `EXTRA-LIBRARY-CREDITS.txt`
og arkiver/konvertering i `source.zip` følger med den færdige app.

For en ny figurudgivelse ændres kun denne app: udskift det relevante
kildeudgivelse, registrer ny størrelse/SHA256 og kontroller CC0-vilkåret i
`sources.json`, tilpas konvertering/test ved ændret arkstruktur, og bump
`../app.json`. Behold upstream-pin og `min_core`, medmindre ændringen faktisk
kræver andet. Kør ovenstående prøve/byg på ny og afprøv figurvalg, animation,
gem/genåbn samt offline drift. Pakning og opdatering beskrives nedenfor.

### Standardbiblioteket

`download-library-assets.ps1` udtrækker Scratchs officielle bibliotekslister og
henter 1.347 standardmedier fra den pinned Scratch assettjeneste: 804 SVG, 193
PNG og 350 WAV. De ligger under `static/library-assets/` og beskrives i
`library-assets-manifest.json`. Efter pakken er hentet, kan standardfigurer,
baggrunde og lyde vælges uden internet fra samme HTTP(S)-origin. Assetserveren
behøver ingen Node.js ved drift.
Listen omfatter nu også de ekstra kostumer; deres lokalt genererede filer
godkendes ved indholdets MD5 og hentes ikke fra Scratchs CDN.

`.sb3`-projekter kan importeres via upload-dialogen og eksporteres via download-
dialogen. Lokal redigering kræver ingen Scratch-konto, cloudprojekt eller
community-adgang. Browseren skal stadig bruge en HTTP(S)-origin til workers og
`fetch`; en simpel fil-URL er ikke understøttet.

Buildet er ikke en erklæring om fuld air-gap-funktion. Cloud/community,
cloudvariabler, kameraansigtsgenkendelse og hardwareudvidelser er valgfrie
funktioner og kan kræve netværk, browser-tilladelser eller eksterne tjenester.
De lokale standardmedier og kerneeditoren ligger i outputtet. En CSP med
`default-src 'self' blob: data:` og `connect-src 'self'` kan bruges til at
verificere, at kerneflowet ikke er afhængigt af udgående netværk.

## Byg kun Scratch-pakken

Appens startfil indlæser `creative/workshops.js` og derefter
`creative/creative-app.js` med `data-workshop="scratch"`. Appens hjælpetekster,
stilark, hjælpebillede og autogem ligger under `../web/` og leveres gennem
`../files.json`; bygget kopierer dem også til preview-outputtet. Metrophobic
leveres af SDK'ets lokale `/assets/metrophobic-latin.ttf`-route. Der medfølger
ingen skolespecifik profil eller logo i Scratchs kildefiler.

Kontrollér navngivning, **Gemt på denne computer** og genåbning fra projekthylden
efter et frisk byg. Den statiske smoke-test kræver nu også appens egne
opstartsfiler og HTTP-routes. Editorinputlåsen udelader `creative/`; disse filer
kontrolleres og tilføjes fra appens eget manifest i næste pakketrin.

Kør fra produktroden på Debian 13 amd64/WSL. `SCRATCH_BUILD` er Linux-stien
til det konkrete Windows-byg, eksempelvis resultatet fra `wslpath -a`.
`OUT` skal være en ny lokal mappe. Der kræves Python 3 og Debian-pakkeværktøjer
på byggemaskinen; elevenheden får ingen nye udviklingsafhængigheder.

```bash
SCRATCH_BUILD='/absolut/linux-sti/til/scratch-gui-v15.1.1-build'
OUT='/var/tmp/scratch-app-candidate'
python -B apps/scratch/build/make_editor_lock.py \
  --build-dir "$SCRATCH_BUILD" --output "$OUT/scratch-editor.json"
python -B system/linux-elevhub/packaging/prepare_app_inputs.py \
  --source-root "$PWD" --app scratch --scratch-lock "$OUT/scratch-editor.json" \
  --output-dir "$OUT/locks"
python -B system/linux-elevhub/packaging/build_debian.py \
  --input-lock "$OUT/locks/makervaerkstedet-scratch.json" \
  --output "$OUT/debian/makervaerkstedet-scratch_15.1.1+local.20261116.3_all.deb"
```

Ret pakkefilens version til den faktiske `../app.json` ved næste opdatering.
`make_editor_lock.py` registrerer hver programfils målsti, mode og SHA256 og
henter version/minimumskerne fra appen. Den private `installation.json` følger
ikke med elevenhedens pakke. Den efterfølgende fælles pakkekommando tilføjer
appens egne webfiler og descriptor og kontrollerer det eksisterende
pakkeejerskab. Ingen hovedsystempakke, historisk filinventar eller tidligere
Scratch-pakke er byggeinput til denne opskrift.

Appudvikleren skal bruge ovenstående værktøjers grænseflader, ikke rette eller
gennemlæse deres implementering. Den gamle `prepare_inputs.py --components
scratch`-vej er til et frosset migrationsinventar og kan afvise nye editorfiler;
brug den ikke til en ny Scratch-appudgivelse.

Den byggede `.deb` er endnu ikke en signeret udgivelse. Følg afsnit 7 og 8 i
[appudviklingsvejledningen](../../../docs/NY-APP-GUIDE.md): behold eksisterende
basis-/app-pakker uændret, udskift kun Scratch i det samlede signerede katalog,
og bestil **Opdatér** for den aftalte testelevenhed. Brug eksisterende signer og
tillidsankre. Kontroller faktisk version, native figurvalg, offline genstart,
gem/genåbn med ekstra kostumer, bevarede projekter og øvrige apps. Kør aldrig
en manuel `dpkg -i` som bevis for den normale læreropdatering.

## Hurtig statisk smoke-test

```powershell
pwsh -File .\apps\scratch\build\smoke-test.ps1
```

Den aktive pilots output har et andet navn end en ny standardbygning. Test
den konkrete installation med:

```powershell
pwsh -File .\apps\scratch\build\smoke-test.ps1 -BuildPath '<LOKAL-TESTMAPPE>\scratch\scratch-gui-v15.1.1-local-projects-build'
```

Testen starter kun et midlertidigt loopback-testserver på port 8799. Den
kontrollerer produktions-standalonebundle, lokale bibliotekmedier, chunks,
favicon og `index.html`; den bruger ikke ElevHubs aktive porte.
