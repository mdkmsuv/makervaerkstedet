# Laser

Elevens laserværksted: tegn former, tekst og importerede SVG'er, vælg skær
eller gravér, og aflevér tegningen til skolens laserkø. App-id `laser`,
loopback-port 8795, projektformat `.laser.json`.

**Dette er ikke Folie.** Filerne `laser.html`, `laser.js` og kommandoen
`npm run build:laser` hører til [Folie](../folie/README.md). Laser ligger
udelukkende i `web/laservaerksted/`.

Med **Lav en spilleplade** kan eleven vælge bane, felter og pynt og indsætte
en plade på 20, 30 eller 37 cm som én gruppe. Gruppen bevarer pladens mål
og de tre handlinger Skær, Gravér streger og Gravér flader. Tegnefladen
udvides efter behov og gemmes i projektet. Lærerens laserstation afgør,
hvor pladen kan placeres på det valgte materiale.
Eleven kan vælge at fjerne det urørte nøgleringseksempel ved indsættelsen.

## Kilde

| Fil | Ansvar |
|---|---|
| `web/laservaerksted/index.html` | Markup og stilark i én fil |
| `web/laservaerksted/laser-editor.js` | Editor, projektadapter, bibliotek og aflevering |
| `web/laservaerksted/board-generator.js` | Offline generator fra Skoleværkstedet, Mathias Kusk Damgaard. Kildehash er angivet i filen. |
| `web/laservaerksted/usb-save.js` | Gem til USB; kun aktiv, når kioskens USB-bro findes |
| `paper-core.min.js`, `opentype.min.js` | Tredjepart med licensfilerne ved siden af |
| `icons/` | Ikoner med eget `manifest.json` |

Der er intet byggetrin. `files.json` pakker **hele** mappen
`web/laservaerksted/` som træ. Alt, der lægges i mappen, følger derfor med
ud på elevcomputeren; læg prøver og noter uden for `web/`.

## Kontrakt med systemet

- `projects-v1` (krævet): eleven gemmer med **Gem projekt** gennem den fælles
  projektbro. Laser har ingen autogem.
- `laser-submit-v1` og `aflevering: laser-submit`: `POST /api/laser/submit`.
  En kvittering betyder modtaget i køen eller gemt i udbakken, aldrig fysisk
  færdig.
- `svg-library-v1`: `GET /api/laser/library`, `/status` og `/file/<sha256>`.
- `theme-v1`: skolens navn, logo og farver fra den lokale profil.

Appværten har en fast genvej for denne app: `/` og `/laser.html` viser
`/laservaerksted/index.html`. Genvejen ligger i
`system/linux-elevhub/projekter/app_host.py` og er en arv fra før
appopdelingen; en ny app får ikke sådan en.

## Prøv ændringer

Kør fra produktroden:

```powershell
python -B sdk/check_app.py --app laser
python -B sdk/preview.py --app laser
python -B -m unittest tests.test_installed_editor_pages tests.test_laser_e2 tests.test_laser_submission_limits -q
```

Forhåndsvisningen bruger den rigtige lokale gateway uden skoleforbindelse.
En aflevering valideres og lægges i en midlertidig udbakke, som slettes ved
Ctrl+C; intet sendes til en kø eller en laser. Biblioteket svarer, at der
ikke er forbindelse.

De renderede prøver mod den installerede appkørsel er
`tests/test_installed_apps_browser.cjs` (sæt `MKV_INSTALLED_APP_FILTER=laser`),
`tests/test_laser_website_library.cjs` og `tests/test_laser_board_generator.cjs`.
De kræver Playwright. På Windows
kører fixturen i WSL; har pc'en ingen WSL-distribution, så sæt
`MKV_QA_PYTHON` til en Python 3.13 eller nyere, og fixturen kører direkte i
Windows. Dér springes to Linux-kontroller over (linksikker mappegennemgang
og root-ejerskab), og rapporten siger det. Ingen af prøverne starter en
fysisk laser.

Editoren serveres kun af den installerede appkørsel. Skoleserveren og de
gamle videresendelsestjenester i `system/app/` svarer 404 på `/laser.html`.
De ældre forløb `tests/test_laser_e2_browser.cjs` og
`tests/test_laser_submission_browser.cjs` henter stadig editoren derfra og
kan ikke køre, før de er flyttet til den installerede appkørsel.

[Opdatér, byg og udgiv appen](../../docs/NY-APP-GUIDE.md) ·
[Kø, udbakke og ansvar](../../docs/APP-PLATFORM.md)
