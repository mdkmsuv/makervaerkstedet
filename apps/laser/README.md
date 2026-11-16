# Laser

Elevens laserværksted: tegn former, tekst og importerede SVG'er, vælg skær
eller gravér, og aflevér tegningen til skolens laserkø. App-id `laser`,
loopback-port 8795, projektformat `.laser.json`.

**Dette er ikke Folie.** Filerne `laser.html`, `laser.js` og kommandoen
`npm run build:laser` hører til [Folie](../folie/README.md). Laser ligger
udelukkende i `web/laservaerksted/`.

## Kilde

| Fil | Ansvar |
|---|---|
| `web/laservaerksted/index.html` | Markup og stilark i én fil |
| `web/laservaerksted/laser-editor.js` | Editor, projektadapter, bibliotek og aflevering |
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
python -B -m unittest tests.test_laser_e2 tests.test_laser_submission_limits -q
```

Forhåndsvisningen bruger den rigtige lokale gateway uden skoleforbindelse.
En aflevering valideres og lægges i en midlertidig udbakke, som slettes ved
Ctrl+C; intet sendes til en kø eller en laser. Biblioteket svarer, at der
ikke er forbindelse.

De renderede prøver mod den installerede appkørsel er
`tests/test_installed_apps_browser.cjs` (sæt `MKV_INSTALLED_APP_FILTER=laser`)
og `tests/test_laser_website_library.cjs`. De kræver Playwright og kører
fixturen i WSL på Windows. Ingen af dem starter en fysisk laser.

De ældre forløb `tests/test_laser_e2_browser.cjs` og
`tests/test_laser_submission_browser.cjs` bygger på de tidligere lokale
servere, som ikke længere finder editorsiderne i kilden efter appopdelingen.
Regn ikke med dem, før de er flyttet til den installerede appkørsel.

[Opdatér, byg og udgiv appen](../../docs/NY-APP-GUIDE.md) ·
[Kø, udbakke og ansvar](../../docs/APP-PLATFORM.md)
