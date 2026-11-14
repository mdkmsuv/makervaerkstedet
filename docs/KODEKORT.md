# Kodekort for Claude og Codex

Kildekort ajourført 14. november 2026. Alle stier nedenfor er fra
produktroden med `REPOSITORY-LAYOUT.json`. Læs først [AGENTS.md](../AGENTS.md)
og [agentindgangen](AGENTINDGANG.md), derefter kun relevante filer.
Søg efter symboler med `rg`; kortet erstatter ikke koden.

## Find den ansvarlige kilde

| Opgave | Kilde og grænse |
|---|---|
| Ny app | [NY-APP-GUIDE.md](NY-APP-GUIDE.md), `sdk/new_app.py`, `sdk/check_app.py`, `sdk/preview.py` |
| Appidentitet og kompatibilitet | `system/app/app_catalog.py`; SDK, capabilities, version, pakke, port og projektformat |
| Appens egne filer | `apps/<id>/app.json`, `files.json` og `web/`; ingen kopi i `system/web/` |
| Apppakke | `system/app/app_packages.py`, `system/linux-elevhub/packaging/prepare_app_inputs.py` |
| Installeret registrering og appvært | `system/linux-elevhub/projekter/app_manifests.py`, `app_host.py`, `template_activation.py`, `runtime_checks.py` |
| Installér, opdatér, fjern, standardsæt | `system/linux-elevhub/server/elevhub/app_admin.py`, `app_admin_http.py`, `web/app-admin.js` |
| Native appudfører | `system/linux-elevhub/administration/app_operations.py`, `apt_client.py` |
| Lærerens åbning/lukning | `system/linux-elevhub/server/elevhub/apps.py`, `tilstand.py`, `elevhub.py`; adskilt fra installeret appbestand |
| Skoleprofil med forhåndsvisning | `system/linux-elevhub/server/elevhub/school_profile_admin.py`, `web/school-profile.js` |
| Lokal skoleprofil og distribution | `system/app/school_appearance.py`, `system/linux-elevhub/server/elevhub/school_appearance.py`, `system/linux-elevhub/administration/school_profile_sync.py` |
| Lokale projekter og elevskift | `system/linux-elevhub/projekter/`; descriptors registrerer nye formater dynamisk |
| Laser/Folie-gateway og udbakke | `system/app/laser_local.py`, `laser_server.py`; udbakke ejes af systemet |
| SVG fra hjemmesiden og lokal cache | `system/app/svg_library.py`, `svg_library_service.py`; appcapability `svg-library-v1` |
| Printkø og printere | `system/app/core.py`, `server.py`, `worker.py`, `printer_bridge.py`, `printer_config.py` |
| Laserstation og fysisk kontrol | `system/app/laser_station.py`, `laser_plate.py`, `laser_material.py`, `laser_transfer.py` |
| Maskiner og workflows | `system/linux-elevhub/server/elevhub/makerspace_*.py`, `web/makerspace*` |
| Serverbootstrap | `system/windows-elevhub/server/bootstrap.py`; starter print, laser og hub |
| Windows-installation | `system/windows/inno/`, `system/windows-elevhub/server/install.ps1`, `deployment/school-setup/` |
| Linux-pakker og native gate | `system/linux-elevhub/packaging/`; [byggeguide](../system/linux-elevhub/packaging/README.md) |
| ISO og ren Debian-installation | `system/linux-elevhub/installation/debian/`; ISO er ikke almindelig opdatering |
| Signeret feed, appkatalog og apt-repository | `scripts/build_simple_feed.py`, `build_app_catalog.py`, `build_apt_repository.py` |
| Kildeeksport og offentlighed | `scripts/export_build_source.py`, `scripts/public_source.py`, `deployment/public-source-manifest.json` |

## De fem appkilder

| App | Rediger her | Byg |
|---|---|---|
| Laser | `apps/laser/web/laservaerksted/` | Statiske filer via appens filliste |
| Folie | `apps/folie/web/` | `npm run build:laser` → `apps/folie/build.mjs` |
| 3D-værksted | `apps/3dprint/web/model3d/`, `model3d.html`, `model3d.css` | `npm run build:model3d` → `apps/3dprint/build.mjs` |
| Scratch | `apps/scratch/web/`, `apps/scratch/build/` | Appens låste upstream-byg og kvittering |
| micro:bit | `apps/microbit/web/` | Statiske filer; MakeCode er ekstern |

`npm run build` bygger systemets viewer og Folie. Ignorerede
browseroutputs skal bygges med de låste afhængigheder og beholde
kilde-/licenskvitteringer. Især 3D kontrolleres af
`apps/3dprint/validate_build.py`. Kildeeksporten kan ikke erstatte en
manglende browserbygning med filer fra en gammel installation.

## Kilde og installerede stier er forskellige

| Kilde | Installeret ansvar |
|---|---|
| `system/app/`, `system/web/` | Windows-serverens `app\` og `web\` samt udvalgte lokale Linux-systemfiler |
| `system/windows-elevhub/server/` | Windows-serverens `server\` |
| `system/linux-elevhub/server/elevhub/` | Windows-serverens `hub\` |
| `system/linux-elevhub/administration/` | `/usr/lib/makervaerkstedet/updater/` |
| `system/linux-elevhub/projekter/` | `/usr/local/lib/elevhub/projekter/` |
| `apps/<id>/` | `/usr/share/makervaerkstedet/apps/<id>/` |

De tidligere logiske `app/`, `web/` og `linux-elevhub/`-stier findes som
byggealiaser i layoutfilen. De er ikke den nye fysiske placering i Git.
Flyt ikke installerede URL'er, data eller imports blot for at matche Git.

Windows-data ligger separat i `C:\ProgramData\ElevhubServer`: print i
`printvaerksted\`, laser i `designvaerksted\`, hub og opdateringsdatabaser
i `hub\`, certifikater i `security\`. Linux har konfiguration under
`/etc/elevhub`, beskyttet updater-tilstand og udbakker under `/var/lib`
samt elevprojekter uden for apppakkerne. Bevar disse ved opdatering.

## Kontrol efter en afgrænset ændring

Kør fra produktroden med projektets Python-miljø. Vælg berørte eksisterende
prøver; samme beståede prøve gentages kun efter relevant ændring.

```powershell
python -B -m unittest discover -s tests -p test_sdk_template.py -v
python -B -m unittest discover -s tests -p test_app_catalog.py -v
python -B -m unittest discover -s tests -p test_app_package_ownership.py -v
python -B -m unittest discover -s system/linux-elevhub/server/elevhub -p test_app_admin.py -v
python -B -m unittest discover -s system/linux-elevhub/tests -p test_app_manifests.py -v
python -B scripts/public_source.py --check
```

Appudførelse, apt, activation og installation prøves på Debian/WSL i de
beskyttede syntetiske miljøer, som testene kræver. Faktisk minisign-prøve
kræver det fastlåste Linux-værktøj i `MKV_TEST_MINISIGN`. Kør
klientmoduler i separate processer som `scripts/run_tests.py`; X11/Tk-
fixtures deler ikke sikkert proces. Kør ejerskabsprøver som almindelig
bruger, når testen kræver det.

En UI-ændring skal ses i den relevante browsertilstand og have uafhængigt
UI/UX-review, også fejl, tastatur, fokus og elevskift. En pakkesimulering
beviser hverken faktisk installation, grafisk opstart, ISO-boot eller
fysisk maskinunderstøttelse. Se [udgivelsesprocessen](UDGIVELSESPROCES.md).

## Undgå de gamle veje

`release_feed.py`, `release_import.py`, `device_updates.py` og de gamle
`linux-queue-v1`-ZIP-opskrifter er historiske elevopdateringer.
Ny Linux-distribution bruger signeret native apt og separat appkatalog.
Ansible-kioskfiler bruges stadig som kildeinputs; det gør ikke Ansible til
den aktuelle appinstallationsvej.

Tilføj ikke en ny SDK-app til gamle `OFFLINE_APPS`- eller formatlister.
Tilføj ikke en permanent kerneafhængighed på valgfrie apps. Lærerens
adgangslukning må ikke forveksles med afinstallation. Køkvitteringer må
aldrig beskrives som fysisk færdigt arbejde.

Nye Markdown-filer skal med i `explicitFiles` i offentlighedsmanifestet;
nye billeder og fonte kræver også godkendt oprindelse og hash. Bevar
[CREDITS.md](../CREDITS.md), [LICENSE-SCOPE.md](../LICENSE-SCOPE.md) og
tredjepartslicenser.
