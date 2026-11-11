# Licenser og provenance før public release

Projektets egne nye dele har nu GPL-3.0-or-later med bevaret kreditering,
mens Elevhubs eksisterende dele beholder MIT. Den fulde tekst og præcise
afgrænsning står i [LICENSE](../LICENSE) og [LICENSE-SCOPE.md](../LICENSE-SCOPE.md).
En source-eksport omfatter egne kilder, patches og notices; den omlicenserer
ikke de lokale runtime-, model- eller installationspakker.

## Ejerens tilladelse og obligatoriske kreditering

Brugeren har oplyst tilladelse til, at projektet må gøres frit tilgængeligt, på betingelse
af kreditering af Klostermarksskolen. Følgende krediteringstekst skal følge
både kildeudgivelsen og enhver installationspakke:

> Kreditering: Klostermarksskolen.

Krediteringen skal også være synlig i programmets Om-/krediteringsflade.
Generisk skolenamekonfiguration eller en generisk public-pakke må ikke fjerne,
skjule eller overskrive denne ophavskreditering.

GPL-valget og den obligatoriske ophavskreditering gælder projektets egne nye
dele som beskrevet i licensafgrænsningen. Tredjepartslicenser, medfølgende aktiver og deres notices
skal fortsat bevare deres egne vilkår; de erstattes ikke af denne kreditering
eller af en ny blanket-`LICENSE`.

## Det, der allerede er dokumenteret

`linux-elevhub/THIRD_PARTY.md` beskriver bl.a. følgende spor:

| Komponent eller aktiv | Afklaring før public release |
|---|---|
| Elevhub-kode | Eksisterende komponent under `linux-elevhub/` beholder MIT; komponentens fulde tekst medfølger. |
| Printværksted og Designværksted | Egne nye kilder har GPL-3.0-or-later med ophavskreditering. |
| Scratch-editor og medier | Kontroller AGPL-forpligtelser, tilsvarende kildekode, medier og varemærker. |
| npm-frontenden | Versionslåst `package-lock.json`; Paper, OpenType, Clipper, ImageTracer og Three har fulde tekster under `web/assets/`. |
| Egne programillustrationer | 37 hashbundne runtime-stier under CC BY 4.0; provenance og fuld tekst i `web/assets/PUBLIC-ILLUSTRATIONS.md` og `CC-BY-4.0.txt`. Skolelogo og private fotos er udeladt. |
| Datatræner | Eksternt runtime-artifact; versionspin og upstream MIT/notices skal følge det konkrete artifact. |
| Boxes.py og CircuitJS1 | Bevar de relevante GPL-tekster og kilde-/noticekrav. |
| CaDoodle | CC0- og LGPL-kildetekster fra de præcise upstream-pins ligger i `linux-elevhub/cadoodle/kloster/licenses/` med kilde-URL og SHA-256. Den officielle JARs øvrige komponentvilkår gælder særskilt. |
| Bambu Studio og øvrige medfølgende værktøjer | Match de bundtede versioner med deres faktiske notices og redistributionsvilkår. |
| Klostermarksskolen i krediteringen | Tilladelsen er oplyst; krediteringen skal bevares. |
| Skolelogo og referencebilleder | Særskilte aktivers tilladelse eller neutral erstatning til generisk udgivelse. |

## Releasekontrol

Før en udgivelse skal manifestet knytte hver medfølgende binær eller model til
version, kilde, licensnotice og checksum. En public source-export må ikke
indeholde `Skolens-printere-privat.json`, private skoleprofiler, elevdata,
private nøgler eller skolebundne referencebilleder.

Releasekontrollen skal desuden kontrollere, at krediteringen i [CREDITS.md](../CREDITS.md)
er med i kildeudgivelsen, installationspakken og programmets synlige
Om-/krediteringsflade.

Hvis en komponent kræver kildeudlevering, skal releasepakken eller dens
følgedokumentation pege på den tilsvarende kilde og de ændringer, der faktisk
er distribueret. En licensefil alene løser ikke dette krav.

### Afgrænsning af denne udgave

AI til kamera-, plade- og laservurdering distribueres ikke.
Ollama, Qwen, MobileSAM og deres inference-runtimes indgår derfor
ikke i nye pakker. Datatræner bevares til undervisning; dets faktiske
afhængigheder og notices følger fortsat det valgte artifact.

## Ejerens beslutninger

- [x] Tilladelse til fri tilgængeliggørelse oplyst, med obligatorisk kreditering af Klostermarksskolen.
- [x] Licens og afgrænsning for projektets egne dele er skrevet.
- [x] Private skolelogoer og referencefotos holdes ude af den generiske source-eksport; ophavskreditering bevares.
- [ ] Godkend alle tredjepartsnotices og eventuelle kildeudleveringer.
- [ ] Godkend hvilke runtime-, model- og værktøjsdownloads der må distribueres.
- [ ] Få en kompetent rettighedsvurdering, hvis projektet skal deles offentligt.
