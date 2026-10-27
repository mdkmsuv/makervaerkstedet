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
er arkiveret. `installation.json` i denne mappe registrerer den faktiske kilde,
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

Kør fra `linux-elevhub`-mappen i PowerShell:

```powershell
pwsh -File .\server\scratch\build-scratch.ps1
```

Scriptet afviser eksisterende source- og outputmapper for at undgå overskrivning.
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
Projektbroen leveres af [den lokale tjeneste](../../projekter/README.md).
Den ændrer ikke upstream-pin eller filer i dette repositorys færdige editor.
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

`download-library-assets.ps1` udtrækker Scratchs officielle bibliotekslister og
henter 1.347 standardmedier fra den pinned Scratch assettjeneste: 804 SVG, 193
PNG og 350 WAV. De ligger under `static/library-assets/` og beskrives i
`library-assets-manifest.json`. Efter pakken er hentet, kan standardfigurer,
baggrunde og lyde vælges uden internet fra samme HTTP(S)-origin. Assetserveren
behøver ingen Node.js ved drift.

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

## Hurtig statisk smoke-test

```powershell
pwsh -File .\server\scratch\smoke-test.ps1
```

Den aktive pilots output har et andet navn end en ny standardbygning. Test
den konkrete installation med:

```powershell
pwsh -File .\server\scratch\smoke-test.ps1 -BuildPath '<LOKAL-TESTMAPPE>\scratch\scratch-gui-v15.1.1-local-projects-build'
```

Testen starter kun et midlertidigt loopback-testserver på port 8799. Den
kontrollerer produktions-standalonebundle, lokale bibliotekmedier, chunks,
favicon og `index.html`; den bruger ikke ElevHubs aktive porte.
