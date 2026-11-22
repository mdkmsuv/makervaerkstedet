# Folie

Elevens folieværksted: tegn eller hent motiver, tilpas dem til skæremåtten,
og gem tegningen som SVG til folieskæreren. App-id `folie`, loopback-port
8796, projektformat `.svg`. Folie afleverer ikke til en kø.

**Navnene snyder.** Folie er bygget på det tidligere fælles tegneværksted, så
kildefilerne hedder `laser.html`, `laser.js` og `laser-*.js`, og
byggekommandoen hedder `npm run build:laser`. Det er alt sammen Folie.
Laserværkstedet er en anden app i [`apps/laser/`](../laser/README.md).
Koden skifter til folietilstand, når værten oplyser `ElevhubApp.id` som
`folie`.

Vælg **Skrifttype** på et tekstobjekt. Alle 12 skrifttyper virker offline,
og projektfilen gemmer valget. [Skrifttyper og licenser](web/assets/workshop-fonts/README.md).

## Kilde og byg

| Fil | Ansvar |
|---|---|
| `web/laser.js` | Indgang til editoren; importerer modulerne nedenfor |
| `web/laser-*.js`, `brother.js`, `vinyl-workshop-ui.js`, `design-*.js` | Geometri, tekst, billeder, bibliotek, måtter og eksport |
| `web/laser-trace-worker.js` | Billedsporing i en worker |
| `web/laser.html` og fire stilark | Siden, som appværten viser på `/` og `/folie.html` |
| `web/vendor/` | **Bygget output.** Ignoreret af Git; redigér det ikke |
| `build.mjs` | Bundler de to indgange med den låste esbuild 0.28.2 |

Byg efter enhver ændring i JavaScript-kilderne, fra produktroden:

```powershell
npm run build:laser
```

Bygget skriver `web/vendor/laser.js`, `laser-trace-worker.js` og kvitteringen
`web-build-manifest.json`. Pakkekontrollen i `scripts/validate_web_build.py`
afviser en pakke, hvis kvitteringen ikke passer til de aktuelle kilder og
`package-lock.json`. Et gammelt `vendor/` kan altså ikke pakkes ved en fejl,
og et manglende `vendor/` får `sdk/check_app.py` til at fejle.

`files.json` oplister hver pakket fil for sig. En ny HTML-, CSS- eller
assetfil skal tilføjes dér; JavaScript-modulerne bundles og skal ikke.

## Kontrakt med systemet

- `projects-v1` (krævet): gem og genåbn gennem den fælles projektbro.
- `svg-library-v1`: `GET /api/folie/library`, `/status` og `/file/<sha256>`.
- `theme-v1`: skolens navn, logo og farver fra den lokale profil.
- Ingen `aflevering`. Den fælles kildekode indeholder stadig kald til
  `/api/laser/submit`, men gatewayen svarer 404, fordi Folie ikke har
  `laser-submit-v1`.

## Prøv ændringer

```powershell
npm run build:laser
python -B sdk/check_app.py --app folie
python -B sdk/preview.py --app folie
python -B -m unittest tests.test_web_build_receipt tests.test_installed_editor_pages tests.test_folie_library_gateway -q
```

Forhåndsvisningen kører uden skoleforbindelse, så biblioteket svarer, at der
ikke er forbindelse. Den renderede prøve mod den installerede appkørsel er
`tests/test_installed_apps_browser.cjs` (sæt `MKV_INSTALLED_APP_FILTER=folie`).
Den kræver Playwright. På Windows kører fixturen i WSL; har pc'en ingen
WSL-distribution, så sæt `MKV_QA_PYTHON` til en Python 3.13 eller nyere, og
fixturen kører direkte i Windows. Dér springes to Linux-kontroller over
(linksikker mappegennemgang og root-ejerskab), og rapporten siger det.

Editoren serveres kun af den installerede appkørsel. Skoleserveren og de
gamle videresendelsestjenester i `system/app/` svarer 404 på `/folie.html`.
De ældre kontroller `scripts/verify_folie_library.mjs`,
`scripts/verify_brother_workshop.mjs` og
`tests/test_folie_project_import_browser.cjs` henter stadig editoren derfra
og kan ikke køre, før de er flyttet til den installerede appkørsel.

[Opdatér, byg og udgiv appen](../../docs/NY-APP-GUIDE.md) ·
[SVG-bibliotek og ansvar](../../docs/APP-PLATFORM.md)
