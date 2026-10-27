# Kreditering og tredjepartslicenser

Elevhub er udviklet af **Mathias Kusk Damgaard**. Projektets egen serverkode,
lærerpanel, elevstartside, opsætningsværktøjer og dokumentation er open source
under [MIT-licensen](LICENSE). Licensens copyright- og tilladelsestekst skal
bevares ved kopiering. Licensen giver ikke rettigheder til tredjepartsnavne,
logoer eller software ud over deres egne vilkår.

| Ressource | Ophav og licens | Hvor den dokumenteres |
|---|---|---|
| Lokal Scratch-editor | Scratch Foundation og bidragydere, GNU AGPL version 3 ved pin `v15.1.1` | [Build, kildekode og tilpasninger](server/scratch/README.md). Upstream `LICENSE` og `TRADEMARK` bevares i buildet. Den ændrede editor skal tilbyde sin tilsvarende kildekode til brugerne. |
| Lokal Datatræner | Center for Computational Thinking and Design, Aarhus Universitet; MIT ved commit `d6fe8045d262553be2e5f67457b440949e048f2f` | [Datatrænerens kilde og build](server/datatraener/README.md). Upstreams copyright og licens medfølger. |
| Metrophobic | The Metrophobic Project Authors; SIL Open Font License 1.1 | [Medfølgende licenstekst](server/elevhub/web/assets/Metrophobic-OFL.txt) |
| Squid, Debian, Chromium, Ansible, restic og andre installerede systempakker | Deres respektive projekter og licenser | De officielle pakkers licensfiler, fx `/usr/share/doc/<pakke>/copyright` på Debian. De bliver ikke omlicenseret af Elevhub. |
| npm-afhængigheder og biblioteksmedier i værkstederne | Respektive rettighedshavere | Buildets licensmeddelelser, package-lock, originale kilde- og mediefiler. MIT-licensen i denne mappe erstatter ikke disse vilkår. |
| Klostermarksskolens navn og logo | Klostermarksskolen | Identitetsmateriale til den konkrete installation, **ikke** omfattet af projektets MIT-licens. Andre skoler skal udskifte navn og logo eller have rettighederne til at bruge dem. |

MakeCode og SkoleGPT åbnes som eksterne hjemmesider og kopieres ikke ind i
Elevhub. Disse tjenester har deres egne vilkår og krediteringer.

Printværkstedet og Designværkstedet er selvstændige backendprojekter uden for
`linux-elevhub`. Denne licens ændrer ikke deres licensvilkår.

De lokale tilpasninger af Scratch indgår i den AGPL-licenserede editor. At
Elevhub-serveren ved siden af editoren har MIT-licens omlicenserer ikke Scratch.
Scratchs [officielle licensvilkår, afsnit 7](https://mitscratch.freshdesk.com/en/support/solutions/articles/4000219182-scratch-terms-of-service)
skelner også mellem kildekode, mediematerialer og varemærker. Bibliotekets
billeder og lyde bliver ikke automatisk MIT- eller AGPL-licenseret af, at
editoren bruger dem. Deres kilde, mediemanifest og oprindelige vilkår skal
følge med; en offentlig samlet pakke kræver kontrol af medielicenserne.
Bevar versionspin, ændret kildekode, licenser og byggeanvisninger ved
viderefordeling. Der er ikke offentliggjort et GitHub-repository som led i
denne licensering; kildekode og licensfiler ligger i projektmappen.

## Designværkstedets JavaScript-biblioteker (laser/folie)

Designværkstedet (laser/folie) kører lokalt på hver elevcomputer via
`app/laser_local.py`. Det er et selvstændigt backendprojekt uden for
`linux-elevhub` (se afsnittet ovenfor om Printværkstedet og
Designværkstedet), men frontend-koden udrulles til de samme
elevcomputere, så bibliotekerne dokumenteres her. Projektrodens
`package.json` bygger frontend-koden med esbuild til to filer i
`web/vendor/`:

- `web/vendor/laser.js` bundler paper.js, opentype.js og clipper-lib (importeret i `web/laser-geometry.js` og `web/laser-offset.js`).
- `web/vendor/laser-trace-worker.js` bundler imagetracerjs (importeret i `web/laser-trace-worker.js`, bygget som separat esbuild-output).
- `web/vendor/viewer.js` bundler three.js, inklusive tilføjelserne `STLLoader` og `OrbitControls` (importeret i `web/viewer.js`).

Versionerne er låst i `package.json`/`package-lock.json` i projektroden
(`<REPOSITORY>`, ikke `linux-elevhub`):

| Bibliotek | Version | Licens (SPDX) | Upstream | Lokal licenstekst |
|---|---|---|---|---|
| paper (paper.js) | 0.12.18 | MIT | https://github.com/paperjs/paper.js | `web/assets/Paper-MIT.txt` |
| opentype.js | 2.0.0 | MIT | https://github.com/opentypejs/opentype.js | `web/assets/OpenType-MIT.txt` |
| clipper-lib | 6.4.2 | BSL-1.0 (Boost Software License 1.0) | https://github.com/junmer/clipper-lib | `web/assets/Clipper-LICENSE.txt` (indeholder også Tom Wus separate JSBN-licens) |
| imagetracerjs | 1.2.6 | Unlicense | https://github.com/jankovicsandras/imagetracerjs | `web/assets/ImageTracer-UNLICENSE.txt` |
| three (three.js) | 0.183.2 | MIT | https://github.com/mrdoob/three.js | `web/assets/Three-MIT.txt` |

Stierne i tabellen er relative til projektroden, ikke til `linux-elevhub`.

## Boxes.py (Superbrugerens boksgenerator)

Laserværkstedets Superbruger-boksfunktion bruger den vendorerede kilde til
[Boxes.py](https://github.com/florianfesti/boxes), fastlåst til commit
`7a6ccc48b3fdd3680fe90c6769f0ec5cf003e30e`. Kilden ligger i
`third_party/boxes.py` (projektroden), og licensen står i
`third_party/boxes.py/LICENSE.txt`: **GNU GPL-3.0-or-later** (projektets
`README.rst` skriver "Boxes.py is free software licensed under GPL v3+").
Serveren kalder den uændrede `boxes.generators.abox.ABox`-generator via
`app/boxes_worker.py` og `app/boxes_generator.py`; se
`third_party/BOXES-PY-SOURCE.md` for integrationsdetaljer. Kildekoden
kopieres til hver elevcomputer sammen med resten af Designværkstedets
serverkode.

## Kredsløbsværksted (circuitjs1)

Kredsløbsværkstedet vendorer [CircuitJS1](https://github.com/pfalstad/circuitjs1)
(**GNU GPL-2.0-or-later**), fastlåst til upstream-commit
`5bdb1296ce6a82f79515f4f1dd1b9a86e03236f7`. Fuld proveniens, licenstekstens
placering (`vendor/circuitjs1/COPYING.txt`) og byggedetaljer står allerede i
[server/kredsloeb/README.md](server/kredsloeb/README.md) og gentages ikke
her.

## CaDoodle-Kloster

`cadoodle/kloster/` (se `build.py`, `prepare_source_cache.py` og
[README.md](cadoodle/kloster/README.md)) bygger en ændret
`CaDoodle-Kloster.jar`, der udrulles til alle elevcomputere. Byggeren tager
den officielle, SHA256-verificerede CaDoodle-Application 0.46.094-JAR,
henter ti Java-kildefiler fra fastlåste Git-commits i tre
CommonWealthRobotics-GitHub-repositorier, patcher dem (`build.py`,
`dimension_input_patch.py`, `drag_resize_patch.py`, `move_input_patch.py`,
`touch_input_patch.py`) og genkompilerer dem mod den officielle JAR.
Resultatet er en kopi af JAR'en med de patchede klassefiler udskiftet;
originalen redigeres ikke.

De tre kildekommitter og deres licenser (slået op 26. september 2026 via
GitHubs offentlige API og raw-filer på netop de fastlåste commits):

| Repository | Fastlåst commit | Bruges til | Licensfil | Licens, som den står |
|---|---|---|---|---|
| [CommonWealthRobotics/CaDoodle-Application](https://github.com/CommonWealthRobotics/CaDoodle-Application) | `f723ccd25dbc2caf6c2818ddaa4c47193d75aa6c` | `MainController.java`, `WorkplaneManager.java`, `ViewCube.java`, `SelectionSession.java`, samt filer hentet af `dimension_input_patch.py`/`move_input_patch.py`/`touch_input_patch.py` (i alt otte filer) | `LICENSE` | **CC0 1.0 Universal** (SPDX: `CC0-1.0`) |
| [CommonWealthRobotics/BowlerStudio](https://github.com/CommonWealthRobotics/BowlerStudio) | `b3db3606c74344d705c9084af8872cbc12f35a0f` | `BowlerStudio3dEngine.java` | `LICENSE` | **GNU Lesser General Public License v3.0** (GitHub angiver SPDX: `LGPL-3.0`) |
| [CommonWealthRobotics/bowler-script-kernel](https://github.com/CommonWealthRobotics/bowler-script-kernel) | `457a64691504f1231ae5e6d5fd1d99577d96a5b4` | `Manipulation.java`, hentet af `move_input_patch.py` | `LICENSE` | **GNU Lesser General Public License v3.0** (GitHub angiver SPDX: `LGPL-3.0`) |

`bowler-script-kernel` er ikke et af de to repositorier, som denne
undersøgelse blev bedt om at slå op, men `move_input_patch.py` og
`prepare_source_cache.py` henter og kompilerer faktisk også kildekode
derfra ind i `CaDoodle-Kloster.jar`, så den er medtaget for fuldstændighedens
skyld.

Alle tre kilde-licensfiler er nu arkiveret i
`linux-elevhub/cadoodle/kloster/licenses/` fra ovenstående præcise commits.
`source-manifest.json` registrerer hver URL og SHA-256. De medfølger den rene
kildeeksport. Følgende beskriver fortsat komponenternes særskilte vilkår:

- **CaDoodle-Application (CC0-1.0):** Ophavsmanden har lagt kildefilerne i
  det fri. CC0 stiller normalt ikke krav om selv at videregive kildekode
  eller ændringer, men det bør bekræftes, at CC0-filen faktisk dækker de
  konkrete filer, der hentes (kun repoets øverste `LICENSE`-fil er slået op).
- **BowlerStudio og bowler-script-kernel (LGPL-3.0):** De patchede filer fra
  disse to repositorier bliver kompileret ind i den udrullede
  `CaDoodle-Kloster.jar`. LGPL er en svag copyleft-licens, som typisk
  indebærer, at bibliotekets kildekode og de foretagne ændringer skal kunne
  udleveres, og at brugeren kan sætte en ændret version af biblioteket ind i
  stedet. Ejeren bør sikre, at de patchede kildefiler (patch-scripts og
  `build.py`) og byggeopskriften kan udleveres på forespørgsel, og bør få
  den præcise forpligtelse bekræftet mod selve licensteksten eller en jurist,
  hvis den skal stå juridisk fast.
- Det er ikke undersøgt, om den installerede
  `CaDoodle-Application.official.jar` selv medfølger en separat
  licens/EULA ud over kilderepositoriets `LICENSE`-fil; kun kilderepoernes
  licensfiler er slået op her.

## Åbne punkter

- Three.js 0.183.2 har nu den fulde uændrede MIT-tekst i
  `web/assets/Three-MIT.txt`, kopieret fra den versionslåste npm-kilde.
- Repositorierne BowlerStudio og bowler-script-kernel har begge den generiske
  LGPLv3-tekst fra FSF som `LICENSE` ved de fastlåste commits (GitHub:
  `LGPL-3.0`). Selve de to filer, der hentes, bærer dog ingen
  LGPL-erklæring. `BowlerStudio3dEngine.java` har i stedet en indlejret
  copyright fra Oracle (2011, 2013) med en BSD-lignende licens til
  videredistribution. Den kræver, at copyrightteksten bevares, og forbyder
  brug af Oracles navn til anbefaling. `Manipulation.java` har ingen licens-
  eller copyrighttekst. Ingen af repositoriernes README nævner
  licensversionen. Om LGPL gælder "kun version 3" eller "version 3 eller
  senere", kan derfor ikke afgøres ud fra kilderne. Ejeren bør få afklaret,
  om Oracle-headeren gælder for netop den fil i stedet for eller i tillæg til
  repositoriets LGPL-3.0 (opgave til ejeren, ikke juridisk rådgivning).
  Oracle-copyrightteksten skal under alle omstændigheder følge med ved
  videredistribution.
- Den officielle CaDoodle-Application-installers egen licens/EULA (den
  distribuerede JAR, ikke GitHub-kilderepoet) er ikke undersøgt.
- Clipper-lib-linket i tabellen er JavaScript-porten (`junmer/clipper-lib`),
  som den lokale licensfil selv henviser til; det oprindelige C++
  Clipper-biblioteks egen hjemmeside/licens er ikke slået separat op.
- esbuild (byggeafhængighed i `devDependencies`) er ikke medtaget i tabellen,
  fordi det kun bruges til at bygge `web/vendor/*.js` og ikke selv udrulles
  til elevcomputerne.
