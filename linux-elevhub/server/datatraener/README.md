# Lokal ultra:bit Datatræner

Datatræneren er previewets lokale build af
`microbit-foundation/cctd-ml-machine`, branch `release-dr`, pinned til commit
`d6fe8045d262553be2e5f67457b440949e048f2f`. Kildecheckoutet ligger uden for
repositoryt i `<LOKAL-TESTMAPPE>\datatraener\cctd-ml-machine-release-dr`, og
buildet installeres i
`<LOKAL-TESTMAPPE>\datatraener\cctd-ml-machine-release-dr-build-final4`.

Det er den aktuelle, verificerede outputmappe; `installation.json` registrerer
den. Scriptets standardnavn på en ny installation er `...-build`. På denne
maskine skal et genbuild have en ny `-OutputDir`, da eksisterende output ikke
overskrives.

Kør fra `linux-elevhub`-mappen; PowerShell bygger webassets til Linux-værkstedet:

```powershell
pwsh -File .\server\datatraener\build-datatraener.ps1
pwsh -File .\server\datatraener\smoke-test.ps1
```

Prøv den aktive pilots eksisterende build med
`pwsh -File .\server\datatraener\smoke-test.ps1 -BuildPath '<LOKAL-TESTMAPPE>\datatraener\cctd-ml-machine-release-dr-build-final4'`.

En ren upstream-checkout kan gøres reproducerbar med den versionerede offline-
patch. Klon den pinned branch, kontrollér committen, og anvend derefter
`offline.patch` før build:

```powershell
git clone --branch release-dr https://github.com/microbit-foundation/cctd-ml-machine <LOKAL-TESTMAPPE>\datatraener\cctd-ml-machine-release-dr
git -C <LOKAL-TESTMAPPE>\datatraener\cctd-ml-machine-release-dr checkout d6fe8045d262553be2e5f67457b440949e048f2f
git -C <LOKAL-TESTMAPPE>\datatraener\cctd-ml-machine-release-dr apply --ignore-space-change --whitespace=nowarn '<REPOSITORY>\linux-elevhub\server\datatraener\offline.patch'
pwsh -File .\server\datatraener\build-datatraener.ps1
```

`offline.patch` indeholder også de låste `package.json`/`package-lock.json`
versioner og fjernelsen af telemetry-/samtykkeflowet. Buildet kopierer
upstream `LICENSE` til `LICENSE.txt` sammen med de statiske assets.

Buildet bruger Vite og bundler ml5, Chart.js og Smoothie fra npm-pakken. Den
lokale ultra:bit-font følger med som statisk asset. D3 indlæses ikke via en
CDN; kildekoden har ingen aktiv D3-import. Application Insights, det gamle
analytics/cookie-samtykkebanner, Google Fonts og eksterne script-tags er fjernet
fra det offline preview. Den lokale forbindelsesflag-cookie bruges fortsat kun
til at genoptage en USB/Bluetooth-forbindelse.

Buildet er MIT-licenseret af Center for Computational Thinking and Design,
Aarhus Universitet (2023), og den upstream-license skal følge outputtet. Det
er en lokal integrationsbuild, ikke en erklæring om at alle hardware- eller
cloudfunktioner virker uden netværk. Bluetooth og micro:bit-firmware skal
afprøves på elevudstyret. Den lokale elevservice serverer buildet på
`127.0.0.1:8793`; den centrale Elevhub serverer ikke Datatræneren og har
ingen projektmappe for den.

### npm-audit og versionsvalg

`package-lock.json` er bevaret og bygges reproducerbart med `npm ci
--ignore-scripts`. Sikkerhedsrettelser uden en API-migration er låst med
overrides: `axios` 1.20.0, `form-data` 4.0.6, `node-fetch` 2.7.0, `postcss`
8.5.28 og `js-cookie` 3.0.8. `@microsoft/applicationinsights-web` er fjernet,
fordi offlinebuildet ikke bruger telemetry.

`npm audit --omit=dev` rapporterer fortsat 12 high-advisories. De væsentlige
runtimekandidater kommer via den pinned `ml5` 0.12.2 og dens bundled
`@tensorflow/tfjs-vis`/Vega-kode; `ml5` 1.x er en breaking migration og må
afprøves særskilt. Resten er primært transitive build- eller Node-fetch-stier.
Kør audit igen ved hver upstream-opdatering og gennemgå Vega-outputtet før en
produktionsinstallation. Der er endnu ikke udført en hardwaretest med en
micro:bit.
Kontrollér tredjepartsafhængigheder og deres sikkerhedsmeddelelser før en
lokal installation.
