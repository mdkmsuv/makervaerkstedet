# Projektåbning

Projektoversigten undersøger filens endelse og i enkelte tilfælde også filens indhold. Når et projekt åbnes, vælger Elevhub automatisk det relevante program eller den relevante visning og sender filen ind i programmet. Brugeren skal derfor normalt ikke vælge værksted manuelt.

## Filtype og program

Mappingen følger `linux-elevhub/projekter/filtyper.py`:

| Filtype eller indhold | Program | Åbningsform |
| --- | --- | --- |
| `.sb3` | Scratch | Lokal redigering |
| `.hex` | MakeCode | Navigerer til MakeCode med projektet indlæst |
| `.stl` | 3D-værkstedet | Printpreview i browseren; CaDoodle bruges kun på computere med native CaDoodle |
| `.klosterskitse` | CaDoodle | Native CaDoodle |
| `.png` | Billedviseren | Visning |
| `.wav`, `.mp3` | Lydafspilleren | Afspilning |
| `.csv` | Tabelviseren | Visning som tabel |
| `.piskel` | Pixelviseren | Visning |
| `.svg` med `data-workshop="vinyl"` | Folieværkstedet | Lokal redigering |
| `.svg` med `data-workshop="laser"` | Laserværkstedet | Lokal redigering |
| `.svg` uden workshop-markering | Laser som standard; Folie hvis uploadens valgte type er Folie | Lokal redigering |
| `.txt` med CircuitJS-header (`$ ...` eller `<cir ...`) | Kredsløb | Lokal redigering |
| anden `.txt` | Tekstviseren | Visning |
| `.json` med Scratch-strukturen (`targets` og `meta`) | Scratch | Lokal redigering |
| `.json` med CaDoodle-strukturen (`marks` og `height`) | CaDoodle | Native CaDoodle |
| anden `.json` | Dataviseren | Visning |

## ZIP-filer

En ZIP-fil åbnes først som Arkivviseren. Hvis den indeholder en `.doodle`-fil i ZIP-filens rod, behandles den som et CaDoodle-projekt. Hvis den indeholder `project.json`, behandles den som en Scratch-pakke. Andre ZIP-filer bliver i Arkivviseren.

## HEX og kildekode

En micro:bit-HEX åbnes kun som MakeCode-projekt, når Elevhub kan finde og udpakke den indlejrede projektkilde. Filen skal derfor være en HEX, der er eksporteret fra MakeCode og indeholder projektfiler som `pxt.json` samt `main.ts`, `main.blocks` eller `main.py`. En firmware-HEX uden indlejret kildekode kan ikke åbnes som redigerbart MakeCode-projekt.

## Redigering og visning

Redigeringsprogrammer får filens bytes sendt automatisk ind efter åbning: Scratch, Kredsløb, Laser, Folie og MakeCode kan fortsætte arbejdet i deres editor. STL-filer vises i Printværkstedets preview på browseren, hvor modellen kan kontrolleres før et eventuelt print.

Visningsprogrammer ændrer ikke projektet: tekst, JSON, CSV, billeder, lyd, Piskel og generiske ZIP-filer åbnes i deres respektive visere. Download af originalfilen er fortsat muligt fra projektoversigten.

SVG er den eneste almindelige filtype med et reelt valg mellem to værksteder. Brug `data-workshop="laser"` eller `data-workshop="vinyl"` i SVG'en, når destinationen skal være entydig. Uden markering bruges Laser som standard, medmindre filen blev gemt med Folie som type.

## Browser-tests kørt

- `node linux-elevhub/tests/test_local_projects_browser.cjs` — PASS: upload, dansk søgning, nøjagtig fil-roundtrip, versionshistorik og 1024×768-layout.
- `node linux-elevhub/tests/test_project_opening_browser.cjs` — PASS: visere, ZIP, SVG til Folie og Laser, Scratch SB3, CircuitJS TXT, MakeCode HEX og STL-preview. Browserfejlrapporten var tom.
