# Projekter på elevcomputeren

Projekthylden og Scratch, Datatræner og Kredsløb kører på elevcomputerens loopback-adresse. Der er ingen central projektmappe, synkronisering eller projekt-upload til Elevhub-serveren. Serveren modtager kun eksplicitte print- og laserafleveringer til køen; lærerstyring bruger små statusbeskeder.

Åbn **Find projekt** på startsiden. Projekterne vises i kategorier efter værksted, med de nyeste først. **Vis alle** folder en kategori ud; søgning på projekt, mappe, forløb eller gruppe og værkstedsfilteret viser alle relevante resultater. Klik på billedet eller **Åbn projekt**. **Hent fil** og **Tidligere versioner** ligger i kortets **⋯**-menu.

Inde i værkstederne bruges samme side, kort og stylesheet, men med fast værkstedsafgrænsning: Laser viser kun laserprojekter, Folie kun folieprojekter osv. Søgning og **Ryd søgning** bliver inden for dette værksted; vælgeren til andre værksteder er skjult. Den fælles oversigt fra startsiden viser fortsat alle kategorier. Filens faktiske værksted bestemmer kategorien, så en gammel forkert etikette på en SVG ikke får et folieprojekt til at dukke op i Laser.

Webværkstederne indlejrer `/_elevhub/projekter/?pick=<type>` og modtager den valgte fil med `elevhub-open-project`. Native værksteder kan åbne `/_elevhub/projekter/?workshop=cadoodle` som selvstændig side; kortet følger da det normale `/open`-forløb. Ukendt værksted giver fejl frem for at vise alle projekter. API'et understøtter tilsvarende `/api/list?workshop=<type>`; brugerfladen bevarer også afgrænsningen mod ældre tjenester, der sender en ufiltreret liste.

Filer gemt i `~/Projekter` bliver fundet automatisk efter to stabile aflæsninger, normalt inden for 10 sekunder. En ændret fil får en ny version; originalen ændres ikke under registreringen. Filer på USB kan tilføjes med **Gem en fil**. **Hent fil** giver en kopi, som kan gemmes på USB. Samme computer bruges næste gang, medmindre eleven tager en kopi med.

## Billeder på projektkortene

Kortet viser seneste gemte indhold, når formatet kan forhåndsvises: PNG, en sikker delmængde af SVG, små STL-modeller, Klosterskitse-tegninger og CaDoodles gemte `snapshot.png` fra den samlede projekt-ZIP. Scratch kan vise et gemt preview eller en figur/baggrund fra projektet, ikke en fuld gengivelse af den kørende scene. Kredsløb, micro:bit og øvrige formater uden et læsbart billede viser et værkstedsikon. En forhåndsvisning ændrer aldrig projektfilen; SVG'er med eksterne billeder og andre ikke understøttede elementer kan falde tilbage til ikonet.

Forhåndsvisninger hentes via den lokale `/api/preview` med projekt-id og version. Indholdet køres ikke som programkode, ZIP-filer pakkes ikke ud, og previews har egne størrelsesgrænser. Hukommelsescachen er begrænset til 8 MiB/128 billeder.

## Automatisk oprydning efter 30 dage

Et projekt udløber **30 døgn efter seneste gemte ændring**. En gemt ændring eller ændring af projektnavn/gruppe giver en ny frist. Åbning, hentning og gentagen gemning af nøjagtigt samme indhold forlænger ikke fristen. En gammel fil, der tilføjes første gang, får 30 døgn fra registreringen. Kortet viser sletedatoen og fremhæver de sidste syv dage. Tag en kopi med **Hent fil**, hvis projektet skal gemmes længere.

Den lokale tjeneste rydder op efter sine to første scanninger og derefter hver time, også uden åbne browsersider eller forbindelse til skoleserveren. Ved udløb slettes projektet og alle registrerede versioner. Tilhørende originalfiler i `~/Projekter` slettes kun, når deres stier, filstørrelser, ændringstider og kontrolsummer stadig stemmer. CaDoodle kræver også uændret indhold af hele den registrerede projektmappe. Ændrede eller usikkert identificerede originaler udsætter sletning af både original og historik. Der følges ingen symbolske links, og andre filer, USB-drev og serverens afleveringskø er uden for oprydningen.

Afbrudt oprydning kan fortsættes efter genstart via en lokal `retention_pending`-tabel. En ny gemt ændring annullerer en tidligere, afbrudt oprydning. Skjulte filer, mapper og uregistrerede filer slettes ikke. Reglen gælder også eksisterende registrerede projekter, når den opdaterede tjeneste starter. Der er ingen papirkurv for udløbne projekter.

Når et værksted åbnes, vælger eleven klasse, skriver fornavn (eventuelt med initial) og projektnavn, eller vælger **Åbn projekt**. Navngivning alene opretter ingen tom projektfil. **Gem projekt** gemmer eksplicit på denne computer under `klasse · fornavn`; navnet skal ikke indtastes igen ved afslutning. Scratch gemmer hele `.sb3` med figurer og lyd. Datatræner gemmer træningsdata og kategorier; modellen skal trænes igen efter åbning. Micro:bit gemmer redigerbare MakeCode-kilder, og eksisterende HEX-filer med indlejret kildekode kan åbnes i samme forløb. Selve MakeCode-editoren hentes fortsat online fra Microsoft; den lokale integration opretter ingen cloudkonto eller central projektmappe.

**Til start**, skift af værksted og lukning bruger samme afslutningskontrol. Ved ændringer er valgene **Gem og afslut**, **Afslut uden at gemme** og **Arbejd videre**. Gemning skal bekræftes, før eleven forlader arbejdet; fejl bevarer det åbne projekt. Afslutning rydder den aktive model, fortryd-historikken og elevens navnefelter. Tidligere gemte projekter bevares efter arkivets almindelige opbevaringsregel. Allerede gemt og uændret arbejde afsluttes uden en ekstra gemmedialog. En ny elev får ikke automatisk sidste elevs projekt åbnet.

Webværkstedernes lokale sikkerhedskopi ved afbrydelse ligger adskilt fra projektarkivet og ændrer aldrig en eksplicit gemt version. Den kan gendannes efter et nedbrud og fjernes ved normal afslutning. CaDoodle arbejder tilsvarende i en privat arbejdskopi og offentliggør først en kopi under `~/Projekter`, når eleven vælger Gem. Genåbning af en ældre version overskriver ikke historikken.

CaDoodles `.klosterskitse` er redigerbar; STL er en model til fremstilling. Et komplet CaDoodle-projekt kan indeholde flere filer og skal gemmes/eksporteres som en samlet pakke gennem CaDoodle. Hylden rekonstruerer ikke automatisk et sådant projekt ud fra en STL.

## Installation og data

`lokal.py` bruger kun Pythons standardbibliotek og SQLite. SQLite er public domain; Elevhubs integrationskode er MIT. Se [SQLite](https://www.sqlite.org/copyright.html) og den eksisterende [restic-backuprolle](../backup/README.md). Der bruges ingen ny cloudtjeneste eller database-server.

- `127.0.0.1:8791`: projekthylde.
- `127.0.0.1:8792`: lokalt installeret Scratch.
- `127.0.0.1:8793`: lokalt installeret Datatræner.
- `127.0.0.1:8794`: lokalt installeret Kredsløb.
- `127.0.0.1:8797`: lokal projektstyring omkring MakeCode-editoren.
- `~/Projekter/.elevhub`: SQLite-indeks og versionsfiler, kun på elevcomputeren.

Ansible-rollen kiosk installerer hele pakken, byggernes appfiler og en systemd-tjeneste under elevkontoen. Tjenesten lytter kun på loopback og afviser fremmede Host/Origin-headers. Ingen projektfiler monteres som eksekverbare websider. Den installerede appkode kan læse den lokale hylde; browsere får ingen CORS-adgang til den fra andre websteder.

Health på projekthyldens port indeholder kun installerede app-id’er og en
versionsmærket navigation (`start`, `scratch`, `datatraener` eller `kredsloeb`).
Elevbjælken sender dette som lokal readiness/visning til Elevhub; projektfiler,
navne og søgninger sendes ikke.

Højst 50 MiB pr. fil og 5 GiB samlet lokal versionshistorik. Når grænsen rammes, viser ny gemning en fejl; projekter, der endnu ikke er udløbet, slettes ikke for at gøre plads. Symlinks, skjulte undermapper og ufuldstændige downloads medtages ikke. Versionshistorikken er fælles for dem, der bruger elevkontoen på computeren, og beholdes indtil projektets slettefrist.

Historikken hjælper ved utilsigtet sletning og ændring. Den er på samme disk som originalerne og erstatter ikke en separat backup. Restic-rollen er fortsat slået fra, indtil et konkret eksternt mål er valgt; Elevhub-serveren skal ikke bruges som backupmål.

## Lokal udviklingsprøve

```powershell
.\.venv\Scripts\python.exe linux-elevhub/projekter/lokal.py --projects output/elevhub-local-preview/Projekter --apps-root output/elevhub-local-preview/apps
```

Appmapperne skal hedde `scratch`, `datatraener` og `kredsloeb` og indeholde `index.html`. Brug `--only-projects` for kun at afprøve hylden. Start ikke dette på serverens LAN-adresse; programmet tilbyder bevidst ingen LAN-bind-indstilling.

Tests: `python -m unittest discover -s linux-elevhub/tests -p test_lokale_projekter.py` dækker filintegritet, versioner, konflikter, fuld disk, stabil filindeksering og HTTP-grænser. `test_local_projects_browser.cjs` afprøver gemning, søgning og hentning mod en isoleret prøvehylde.

`test_project_retention.py` dækker frister og sikker sletning, `test_project_previews.py` previewformater og grænser, og `test_project_gallery_http.py` HTTP-adgang og versionsvalg. `node linux-elevhub/tests/test_project_gallery.cjs` starter sin egen midlertidige hylde og kontrollerer kategorier, søgning, billeder, upload/hentning, historik, genåbning, projektvælger og fire skærmbredder uden at styre brugerens skærm. Brug en versionsmærket, kontrolleret opdateringspakke og et aftalt vedligeholdelsesvindue til installation.
