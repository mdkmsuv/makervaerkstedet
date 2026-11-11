# Kreditering og tredjepartslicenser

Elevhub er udviklet af **Mathias Kusk Damgaard**. Projektets egen serverkode,
lærerpanel, elevstartside, opsætningsværktøjer og dokumentation er open source
under [MIT-licensen](LICENSE). Licensens copyright- og tilladelsestekst skal
bevares ved kopiering. Licensen giver ikke rettigheder til tredjepartsnavne,
logoer eller software ud over deres egne vilkår.

| Ressource | Ophav og licens | Hvor den dokumenteres |
|---|---|---|
| Lokal Scratch-editor | Scratch Foundation og bidragydere, GNU AGPL version 3 ved pin `v15.1.1` | [Build, kildekode og tilpasninger](server/scratch/README.md). Upstream `LICENSE` og `TRADEMARK` bevares i buildet. Den ændrede editor skal tilbyde sin tilsvarende kildekode til brugerne. |
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

## Selvstændig 3D-webprototype

- manifold-3d **3.5.4**, Apache-2.0, https://github.com/elalish/manifold/releases/tag/v3.5.4 . Officiel npm-pakke låses med SHA512-integritet i package-lock.json; den lokale WASM har SHA25673e3b419ad31294f6b1cc478173944e50efc4a34bfdeeff0107bdfc12975f11c. Original Apache-2.0-licens: web/assets/licenses/model3d/manifold-3d-LICENSE.txt.
- three **0.183.2**, MIT, https://github.com/mrdoob/three.js . Original licens: web/assets/licenses/model3d/three-LICENSE.txt.
- esbuild **0.28.2** er kun byggeværktøj. Kommandoen npm run build:model3d bygger særskilte lokale prototypeassets og ændrer ikke de eksisterende værksteders bundles.
- Tekstfigurer genbruger opentype.js **2.0.0**, MIT, https://github.com/opentypejs/opentype.js . Den eksakte ESM og npm-integritet valideres ved bygning; original licens kopieres til web/assets/licenses/model3d/opentype-LICENSE.txt.
- Metrophobic Regular, SIL OFL 1.1, fra https://github.com/googlefonts/MetrophobicFont/tree/d4da54632ddd53631332a5a469183c887b4ea3e1 . Uændret TrueType-font, OFL og præcis kilde/hash følger i web/assets/model3d/. Den fulde font understøtter dansk ÆØÅæøå og er lokal under brug. Den eksisterende latin-font bevares.
