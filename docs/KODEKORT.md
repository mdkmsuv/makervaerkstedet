# Kodekort for agenter

Læs dette før en ændring. Det fortæller, hvor tingene ligger, hvad der hænger sammen, og hvad der skal ændres og testes for de almindelige opgaver,
så du ikke behøver at læse hele kilden. Når du har fundet stedet, så læs den konkrete fil; kortet erstatter ikke koden.

Henvisninger er til fil og funktion/symbol, ikke linjenumre, fordi de flytter sig. Søg efter symbolet med `rg`.

Relaterede dokumenter: [opdateringsguiden](OPDATERINGSGUIDE.md) (byg, test, udgiv) og [udgivelsesprocessen](UDGIVELSESPROCES.md) (regler og ansvar).
Opdatér dette kort, når du flytter, omdøber eller tilføjer en komponent.

---

## 1. Systemet på én side

```
                        Windows-server (C:\Program Files\ElevhubServer + C:\ProgramData\ElevhubServer)
 planlagt opgave ElevhubServer (LOCAL SERVICE)
   └─ server\bootstrap.py ── starter og overvåger tre processer:
        ├─ app.background → app.server      3D-print: kø, slicer, printere    127.0.0.1:8765
        ├─ app.laser_server                 laser/folie-afleveringer           127.0.0.1:8766 (elev), :8767 (lærer)
        └─ hub/elevhub.py                   lærerpanel + indgange for elever   8770-8773, 8780
 planlagt opgave ElevhubSchoolEnrollment (SYSTEM)
   └─ deployment\school-setup\enrollment.py serve    tilmelding af elevcomputere      8774

                        Laserstation (Windows, almindelig bruger, %LOCALAPPDATA%\MakervaerkstedetLaserstation)
   app.laser_station          plade, materiale, overførsel til Beam Studio     127.0.0.1:8790
   app.makervaerkstedet       startsiden (laser / 3D-print / administration)

                        Elevcomputer (Debian 13, apt-pakker)
   kiosk-session → Chromium med start.html, elevbjaelke.py, matchbox
   elevhub-lokal  → projekter/lokal.py          lokale projekter               127.0.0.1:8791
   elevhub-design → app.laser_local             lokal laser/folie/3D (offline) 127.0.0.1:8795/8796/8798
   makervaerkstedet-apt-update.timer → apt_client.py   henter opdateringer fra serveren hvert minut
```

| Port | Hvem lytter | Hvem bruger den |
|---|---|---|
| 8765 | `app.server` (print) | kun hubben (loopback) |
| 8766 / 8767 | `app.laser_server` (elev-/lærerflade) | hubben og laserstationen |
| 8770 / 8771 / 8772 | hubbens elevindgange: 3D-print / Laser / Folie | elevernes browser; videresendes til 8765/8766 |
| 8773 | hubben, enhedsporten | elevcomputernes status, politik og `/apt/` |
| 8774 | `enrollment.py` | tilmelding af nye elevcomputere |
| 8780 | hubben, lærerporten | lærerpanel (`/kontrolpanel`), maskinadministration (`/makerspace`) |
| 8790 | laserstationen | kun loopback på lasercomputeren |

Portene 8765-8773 og 8780 er låst flere steder (8774 og 8790 hører til tilmeldingen og laserstationen): `windows-elevhub/server/bootstrap.py` (`PORTS`), `windows-elevhub/server/install.ps1`, `deployment/school-setup/Setup-School.ps1`
og `windows-elevhub/server/hub/konfiguration.windows.json`. Ændr dem samlet eller slet ikke.

---

## 2. Kilde → installeret placering

| Kildemappe | Windows-server | Elevcomputer | Laserstation |
|---|---|---|---|
| `app/` | `app\` | dele af `/usr/local/lib/elevhub/designvaerksted/app/` | `versions\<id>\program\app\` (kun `APP_FILES`) |
| `web/` | `web\` | dele af `/usr/local/lib/elevhub/designvaerksted/web/` | `…\program\web\` (kun `WEB_FILES`) |
| `linux-elevhub/server/elevhub/` | `hub\` | `web/assets/*` → `/usr/local/share/elevhub/assets/` | – |
| `windows-elevhub/server/` | `server\` | – | – |
| `deployment/school-setup/` | `deployment\school-setup\` | – | scripts til installation |
| `linux-elevhub/ansible/roles/kiosk/files/*.py` | – | `/usr/local/lib/elevhub/` | – |
| `linux-elevhub/ansible/roles/kiosk/templates/` | – | `/usr/share/makervaerkstedet/templates/` | – |
| `linux-elevhub/projekter/` | – | `/usr/local/lib/elevhub/projekter/` | – |
| `linux-elevhub/administration/` (apt-klient m.m.) | – | `/usr/lib/makervaerkstedet/updater/` | – |
| `linux-elevhub/server/microbit-controller/` | – | `/usr/local/share/elevhub/apps/microbit/` | – |
| `mkv2/` (fire filer: `__init__.py` og tre i `control_plane/`) | `mkv2\` | – | – |
| `../byggeinput/windows/*` (uden for `produkt/`) | `runtime\`, `tools\`, `licenses\` | – | samme runtime |

Hvilke filer der kommer med:
- **Windows-server:** alt i mapperne, minus tests (`windows-elevhub/build_packages.py`, `build_server`; `scripts/build_school_package.py`).
- **Elevcomputer:** kun det, `linux-elevhub/packaging/prepare_inputs.py` udvælger. Designværkstedets filer vælges ud fra listen `kiosk_lokal_design_filer` i `linux-elevhub/ansible/roles/kiosk/tasks/main.yml`.
- **Laserstation:** kun `APP_FILES`, `WEB_FILES` og `REQUIRED` i `app/laser_station_install.py`.

**Konsekvens:** importerer du et nyt modul i `app/laser_station.py` eller i noget, elevcomputerens tegneværksted bruger, skal det også ind på den relevante liste. Ellers virker det på serveren og fejler derude.

---

## 3. Komponenterne

### 3.1 3D-print (`app/`)
- `server.py`: HTTP (`Application`, `Handler`). `do_GET`/`do_POST` indeholder alle ruter.
- `core.py`: køen (`Queue`) og hele printforløbet; SQLite-skema i `_initialize_database`.
- `worker.py` (slicer og overførsel), `slicer.py`, `bambu.py` (printerens LAN-protokol), `printer_bridge.py` og `printer_config.py` (kommandoer fra lærerpanelet).
- Hjælpere: `stl.py`, `submission_stage.py` (sikker upload), `queue_storage.py` (oprydning af jobfiler), `file_library.py` (STL-bibliotek).
- `teacher_auth.py`: lærerlogin til print; accepterer også den fælles lærerkode.
- Øvrige: `camera.py`, `firmware.py`, `discovery.py`, `maintenance.py` (opdateringens køspærring), `light_service.py`, `printer_lights.py`.

### 3.2 Laser og folie (`app/`)
- `laser_server.py`: afleveringer fra elever; to flader (elev 8766, lærer 8767).
- `laser_workflows.py`, `laser_inbox_lifecycle.py`, `laser_images.py`, `boxes_generator.py` + `boxes_worker.py`.
- `laser_local.py`: lokale laser-/folieflader og 3D på 8798. `Outbox` bevarer laserafleveringer og print i separate mapper; `PrintForwardingInbox` sender binær STL til hubbens `/api/jobs`. Laser/folie arver `laser_server.Handler`; 3D har egne afgrænsede ruter og WASM/worker-CSP.

### 3.3 Laserstation (`app/`)
- `laser_station.py` (lokal server og Beam Studio-overførsel), `laser_plate.py`, `laser_material.py`, `laser_scene.py`, `laser_ghost.py`, `laser_transfer.py`.
- `laser_machines.py` + `laser_presets_*.json`: lasermodellerne (`fbb2`, `fhexa1`).
- `makervaerkstedet.py`: startsiden. `laser_station_install.py`: installation og versioner.

### 3.4 Hubben (`linux-elevhub/server/elevhub/`, installeret som `hub\` på serveren)
- `elevhub.py`: alle porte og ruter (`Indgang`, `Hub`, `Laerer`, `LokaltVaerksted`).
- `tilstand.py`: åbne værksteder, computere og lærerkoden (`Laererkode`, gemt i `laerer.json`).
- `apps.py`: validering af værkstedslisten og `RETIRED_APPS`. `regler.py`: hvilke stier hver elevindgang må videresende.
- `makerspace_*.py`: maskinadministrationen. `makerspace_api` er facaden, `makerspace_store` er databasen, `makerspace_workflows` validerer, adaptere til printere og laser.
- `simple_updates.py` (hvilken version hver computer skal have, `/apt/`) og `simple_update_import.py` (henter udgivelser fra GitHub).
- Øvrige: `sikkerhed.py` (enhedsregister, TLS), `pause.py` (skærmpause), `system_overview.py`, `server_restart.py`, `school_appearance.py`, `laser_station_gateway.py`.
- Web (`web/`):
  - `kontrolpanel.html/.js/.css` er lærerpanelet på `/` og `/kontrolpanel`.
  - `makerspace.html` + `makerspace-*.js` er maskinadministrationen.
  - `update-admin.js` er opdateringsafsnittet.
  - `assets/` er elevstartsidens grafik og `workshops.js`/`creative-*.js`.

### 3.5 Elevcomputeren (`linux-elevhub/`)
- `ansible/roles/kiosk/files/`: `elevbjaelke.py` (bjælken og forbindelsen til serveren), `elevhub-regler.py`, webproxy, pauseoverlay, startskærm, strømstyring.
- `ansible/roles/kiosk/templates/`: `start.html.j2` (startsiden), `kiosk-session.j2`, systemd-tjenester, Chromium-politik.
- `projekter/`: lokal projekthylde, åbning af projekter i det rigtige værksted.
- `administration/`: `apt_client.py`, `template_activation.py`, `runtime_checks.py`, `os_maintenance.py`.
- `installation/debian/`: ny ren installation (`build_iso.py`, `install_payload.py`, `enroll_device.py`) og overgang fra ældre installation (`bootstrap_existing.py`).
- `packaging/`: byg af de fire `.deb`-pakker og installationsporten (`build_from_source.sh`).

### 3.6 Windows-installation
Kæden: `windows/inno/Makervaerkstedet.iss` → `windows/inno/lifecycle.py` (`run`) →
- **Ny installation:** `deployment/school-setup/Setup-School.ps1` → `windows-elevhub/server/install.ps1`.
- **Opdatering:** `lifecycle.py` `update_server` + `windows/inno/task_control.ps1`.

`deployment/school-setup/` er skolens administrationsværktøjer:
- `Manage-School.ps1`: menu med tilmelding, koder, backup, TLS og genstart.
- `enrollment.py`: tilmelding.
- `Setup-LaserStation.ps1`: laserstationen.

---

## 4. Data: hvor ligger hvad

Under `C:\ProgramData\ElevhubServer\`:

| Mappe | Indhold | Skema oprettes i |
|---|---|---|
| `printvaerksted\` | `queue.sqlite3` (job, indstillinger, køspærring, bibliotek), `laererkode.txt`, `uploads\`, `sliced\`, `library\` | `app/core.py` `_initialize_database`, `app/file_library.py`, `app/maintenance.py` `ensure_schema` |
| `designvaerksted\` | `afleveringer.sqlite3` (afleveringer, laserstationer, workflows), `laererkode.txt` | `app/laser_server.py`, `app/laser_workflows.py`, `app/laser_inbox_lifecycle.py` |
| `hub\` | `laerer.json` (den fælles lærerkode, PBKDF2-hash), `tilstand.json`, `computere.json`, `pause.json`, `makerspace.sqlite3`, `simple-updates.sqlite3` | `tilstand.py`, `makerspace_store.py`, `simple_updates.py` |
| `security\` | CA, servercertifikat, `enheder.json` | `windows-elevhub/server/provision.py` |
| `installer-updates\<id>\` | journal for hver opdatering (`state.json`, `program-before\`, `databases-manual\`) | `windows/inno/lifecycle.py` |
| rod | `installation-owner.json`, `konfiguration.json`, `school.json` | `install.ps1`, `Setup-School.ps1` |

Print-, laser- og makerspace-databasen står på `user_version=1`, og `app/data_contract.py` kræver det (rollerne `print`, `laser`, `hub`). `simple-updates.sqlite3` har ingen version. En skemaændring ændres sammen med `app/database_migrations.py`, og den forrige version skal kunne læse databasen bagefter.

---

## 5. Opskrifter: almindelige ændringer

### 5.1 Ændre et eksisterende værksted
1. Find værkstedet:
   - 3D-print er `web/index.html` + `web/app.js` (eleven) og `app/server.py` (serveren).
   - Laser og folie er `web/laser.html` + `web/laser.js` (bygges til `web/vendor/laser.js`) og `app/laser_server.py`.
   - 3D-tegneværkstedet er `web/model3d.html` + `web/model3d/*.js` (bygges til `web/vendor/model3d-*`).
   - Scratch er en ekstern build (`byggeinput/linux/scratch-…`); kun skolens skal ligger i `linux-elevhub/server/style-workshop.py`.
   - micro:bit er `linux-elevhub/server/microbit-controller/`.
2. Byg JavaScript: `npm run build` (viewer og laser) eller `npm run build:model3d`. `build:model3d` afviser afvigende versioner og hashes; se `scripts/build_model3d_vendor.mjs`.
3. Bruges filen også lokalt på elevcomputeren (laser/folie offline)? Så skal den med i elevpakken (se 5.7).
4. Test: `tests/test_laser_*` for laser, `tests/test_queue*`, `tests/test_http` for print, `tests/model3d-*.test.mjs` (`node --test`) for 3D-tegning.
5. UI-ændringer skal ses i en browser og vurderes efter projektets UI/UX-krav.

### 5.2 Tilføje eller fjerne et værksted
Værkstedslisten findes i dag flere steder, og alle skal følges ad:
- `linux-elevhub/server/elevhub/konfiguration.json` og `windows-elevhub/server/hub/konfiguration.windows.json` (`apps[]`: `id`, `navn`, `type`, `port`, `bagved`, `regler`, …).
- `linux-elevhub/server/elevhub/regler.py` (hvilke stier en elevindgang må videresende).
- `linux-elevhub/ansible/roles/kiosk/templates/start.html.j2` (`OFFLINE_APPS`: hvad der vises uden server) og `project-workshops.json.j2`.
- `linux-elevhub/server/elevhub/web/assets/workshops.js` og `creative-hub.js`.
- Projektåbning: `linux-elevhub/projekter/filtyper.py`, `project_open.py`, `projektarkiv.py`, `workshop_sessions.py`, `web/projekter.js`.

For at fjerne et værksted tilføjes det desuden til `RETIRED_APPS` (`apps.py`), `retiredWorkshops` (`creative-hub.js`) og udelukkelserne i `linux-elevhub/packaging/build_debian.py`. Fjern ikke projektåbningen, så elevernes gamle projekter stadig kan ses.

Test: `test_vaerksteder.py` og `test_retired_apps.py` i `linux-elevhub/server/elevhub/`.

### 5.3 Ny HTTP-rute
- **Print (`app/server.py`):** kun POST er lærerbeskyttet som standard. En ny GET-rute, der viser lærerdata, skal selv kalde `self.require_admin()`. Alt andet under `web/` serveres frit som statiske filer.
- **Laser (`app/laser_server.py`):** en ny elevrute skal i `student_routes`, en ny statisk fil i `surface_files`. Lærerruter kræver `teacher()`.
- **Hubben (`elevhub.py`, klassen `Laerer`):** lærerruter kræver `logget_ind`. POST kræver `Origin` og `Content-Type: application/json`. En ny statisk fil til lærerpanelet skal på listen `LAERER_FILER`.
- **Interne ruter** mellem tjenesterne bruger en Bearer-nøgle på loopback (`require_bridge` i print, `require_makerspace` i laser). Lærerens browser må aldrig kunne kalde dem.

### 5.4 Ændre lærerpanelet eller maskinadministrationen
- Panelet er `linux-elevhub/server/elevhub/web/kontrolpanel.*` og `update-admin.js`.
- Maskinadministrationen er `web/makerspace*.{html,js,css}` og `makerspace_api.py`.
- `laerer.html` bruges ikke længere; byg ikke videre på den.
- Test: `test_kontrolpanel_routes.py`, `test_makerspace_*.py` og browserprøver i `linux-elevhub/tests/*.cjs`. Se panelet i browseren før færdigmelding.

### 5.5 Ændre databaseskemaet
Læs afsnit 4. Tilføj kolonner, så den gamle kode stadig kan læse databasen. Opdatér `app/data_contract.py` og `app/database_migrations.py`, og kør `tests/test_data_contract.py`, `test_database_*` og `test_backup_*`.
Overvej, om en opdatering skal kunne rulles tilbage; journalen gemmer databaserne, men tilbagerulning gendanner aldrig data automatisk.

### 5.6 Ændre installeren eller opdateringen
Filerne er `windows/inno/lifecycle.py`, `task_control.ps1`, `Makervaerkstedet.iss`, `build.py`, `windows-elevhub/server/install.ps1` og `deployment/school-setup/Setup-School.ps1`. Regler:
- Opgavernes navn, konto, beskrivelse og argumenter skal matche præcis mellem `install.ps1`/`Setup-School.ps1` og kontrollen i `task_control.ps1`. Ellers nægter opdateringen at stoppe opgaverne.
- Køen sættes på pause før stop og står på pause bagefter. Opdateringens egen spærring ophæves til sidst (`release_hold`). Der sendes aldrig en fysisk stopkommando til en printer.
- Hvis en proces kører fra programmets `runtime\`, afvises opdateringen; ingen proces dræbes ved navn.
- Kør `tests/test_windows_lifecycle_launcher.py`, `test_windows_task_identity.py`, `test_windows_installer_*` og derefter **altid** VM-testen (`windows/inno/Test-InstallOnVm.ps1`, se opdateringsguiden).
- Få en uafhængig review af ændringen; installeren er det sted, hvor en fejl standser skolens server.

### 5.7 Ændre noget, der skal ud på elevcomputeren
1. Ret kilden i `linux-elevhub/…` (eller i `app/`/`web/`, hvis filen indgår i designværkstedet).
2. Kontrollér, at filen kommer med i `linux-elevhub/packaging/prepare_inputs.py`.
3. Hæv `CORE_VERSION` (og `UPDATER_VERSION`, hvis updateren er ændret).
4. Byg med `build_from_source.sh` i WSL, og sammenlign `*.inputs.json` med forrige udgivelse.
5. Tests i `linux-elevhub/tests/`; flere kræver Linux eller root (se afsnit 7).

`prepare_inputs.py` læser designlisten i `tasks/main.yml` med et fast mønster (seks mellemrum, `- sti`). Ændres formatet, bryder pakken.

### 5.8 Ændre laserstationen
Filerne er `app/laser_station*.py`, `web/laserstation.*` og `web/makervaerkstedet.*`. Tilføjer du en fil eller en import, skal den på listerne i `app/laser_station_install.py`.
`app/makervaerkstedet.py` samler startsiden med laserstationens side indsat og afviser `<script>` i den indsatte del; laserstationens scripts skal ligge i `<head>`.
Test: `tests/test_laser_station*.py`, `test_laser_transfer.py`, `test_makervaerkstedet.py`. Laseren startes aldrig i en test.

### 5.9 Tilføje en fil til kilden
Kør `python -B scripts/public_source.py --check`.
- Melder den "Ukendt valgt kildefil", skal stien i `explicitFiles` i `deployment/public-source-manifest.json`. Det gælder altid `.md` og `.txt` og alt uden for `sourceRoots`.
- Billeder og skrifttyper kræver desuden en post i `publicImages` eller `publicFonts` med hash og oprindelse.
- Skoleoplysninger hører aldrig hjemme i kilden. De ligger i `skole-privat/` uden for repoet.

---

## 6. Ting, der skal matche på tværs af filer

| Hvad | Hvor |
|---|---|
| Portene | `bootstrap.py` `PORTS`, `install.ps1`, `Setup-School.ps1`, `konfiguration.windows.json` |
| Opgavernes argumenter og konti | `install.ps1`, `Setup-School.ps1` ↔ `windows/inno/task_control.ps1` |
| `school_appearance.py` | byte-identisk i `app/` og `linux-elevhub/server/elevhub/` |
| `app/runtime_health.py` | hash fastlåst i `linux-elevhub/packaging/prepare_inputs.py` |
| `app/simple_release.py` | hash fastlåst i `prepare_inputs.py` (updater-pakken) |
| apt-nøglens hash `5b9ee5f0…` | `bootstrap_existing.py`, `build_iso.py`, `install_payload.py`, `template_activation.py`, `prepare_inputs.py`, `updater-pins.json`, `scripts/public_source.py`, `deployment/keys/public-provenance.json` (søg efter præfikset; listen er ikke nødvendigvis komplet) |
| minisign-hash `2c74dffc…` | `apt_client.py`, `prepare_inputs.py`, `updater-pins.json` |
| Pakkenavne og komponenter | `app/simple_release.py` `DEBIAN_COMPONENTS`, `simple_updates.py`, `bootstrap_existing.py`, `prepare_inputs.py` |
| Elevpakkernes versioner | `CORE_VERSION`/`UPDATER_VERSION` i `prepare_inputs.py`; updateren kræver kerne ≥ samme version |
| Den fælles lærerkode | `hub/laerer.json`, læses af print og laser via `teacher_auth.shared_teacher_code_file` |
| Udgåede værksteder | `apps.py` `RETIRED_APPS`, `creative-hub.js`, `build_debian.py`, `start.html.j2` |
| Python- og byggeinput | `PYTHON_HASH` i `windows-elevhub/build_packages.py`, `../byggeinput/windows/artifact-manifest.json` |

---

## 7. Tests: hvad dækker hvad

Kør i testmiljøet (se opdateringsguiden 2.3). De kendte fejl står i `tests/KENDTE-FEJL.txt`.

| Område | Kommando (fra `produkt/`) |
|---|---|
| Print og kø | `python -B -m unittest tests.test_queue tests.test_http tests.test_print_auth` (og `test_printer_*`, `test_bambu_*`) |
| Laser og folie | `python -B -m unittest discover -s tests -p "test_laser_*.py" -t .` |
| Laserstation | `python -B -m unittest tests.test_laser_station_install tests.test_laser_transfer tests.test_makervaerkstedet` |
| Installer og opdatering | `python -B -m unittest discover -s tests -p "test_windows_*.py" -t .` og `windows-elevhub/server/tests` |
| Skoleopsætning | `python -B -m unittest discover -s deployment/school-setup -p "test_*.py"` og `tests/test_school_*.py` |
| Hubben | `cd linux-elevhub/server/elevhub` og `python -B -m unittest test_elevhub test_sikkerhed test_kontrolpanel_routes test_simple_updates` |
| Elevcomputeren | `cd linux-elevhub/tests` og `python -B -m unittest discover -p "test_*.py"` |
| Offentlighed | `python -B scripts/public_source.py --check-repository` |
| Browser | `node <fil>.cjs`, `node --test tests/model3d-*.test.mjs` |
| Alt for Windows | `python scripts/run_tests.py --no-wsl` |

Kræver Linux og root (kør i WSL med `-u root`):
- `test_apt_client`, `test_template_activation`, `test_bootstrap_existing`, `installation/debian/test_installation.py`.
- `tests/test_apt_release_gate.py` og elevpakkernes installationsport.

---

## 8. Gammelt: byg ikke videre på det

| Del | Status |
|---|---|
| `scripts/build_github_release.py`, `scripts/build_update_manifest.py`, `scripts/github_update_service/`, `scripts/school_server_update/`, `scripts/queued_laser_update/` | Den gamle ZIP-opdateringsvej. GitHubs workflows bruger dem stadig. `windows/inno/build.py` importerer én funktion fra `build_update_manifest.py`. |
| `linux-elevhub/server/elevhub/update_admin.py`, `release_feed.py`, `release_import.py`, `device_updates.py` | Den gamle elevopdatering. Slås fra, når `simple_updates` er konfigureret (ruterne svarer 410). |
| `app/generation_*.py`, `deployment/school-setup/Maintain-School.*` | Generationslayoutet; ikke brugt af den nye installer, men installeret på serveren. |
| `linux-elevhub/installation/build.py`, `firstboot.py`, `onboarding.py` | Gammel USB-installation; den nye ligger i `installation/debian/`. |
| Ansible-vejen (`linux-elevhub/ansible/`, `ansible/koer.sh`) | Den ældre måde at opdatere elever på. Filerne i `roles/kiosk/` er stadig kilde til elevpakken, men nye funktioner udrulles med apt. |
| `linux-elevhub/server/elevhub/web/laerer.html`, `laerer.js` | Erstattet af `kontrolpanel.html`. `laerer.html` har ingen rute, men `/laerer.css` og `/laerer.js` serveres stadig (`laerer.css` bruges af `om.html`). |
| `app/laser_service.py`, `app/desktop.py` | Bruges kun af tests og gamle scripts. |
| `produkt/slet/` | Udgåede filer, ikke med i git. Slettes, når de er gennemset. |

---

## 9. Kendte uafklarede forhold

- Print og laser opretter stadig hver sin egen lærerkode (`laererkode.txt`) ved siden af den fælles kode i `hub/laerer.json`. De accepterer begge, og `Manage-School` viser de lokale.
- Nøglerne `simple_updates` og `makerspace_*` i `konfiguration.json` skrives af installationen og `task_control.ps1 ConfigureSimple`. De står ikke i skabelonerne.
- `windows/inno/build.py --role laser_station` er ikke bygget eller afprøvet.
