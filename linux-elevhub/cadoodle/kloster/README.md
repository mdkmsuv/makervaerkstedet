# Kloster-værktøjer til CaDoodle

Kreditering: Klostermarksskolen og Mathias Kusk Damgaard.

Lokale CaDoodle-værktøjer til skitser, ekstrudering og aflevering til printkøen.
Skolens udseende vælges gennem den lokale profil; det offentlige standardtema er generisk.

## Elevens værktøjer

### Send til ledig printer

Knappen viser en bekræftelsesboks: **“Er du sikker på, at du er færdig med din tegning og vil sende den til print på en af de ledige printere?”** Eleven vælger **Nej, tegn videre** eller **Ja, send til print**. Nej og Esc sender intet. Hele den synlige model sendes, uanset hvilke figurer der er markeret.

Efter bekræftelse tages en kopi af projektets synlige, solide figurer. De gemmes som én STL med deres indbyrdes placering bevaret. Skjulte figurer, løse hulfigurer, indhold skjult af en gruppe og referencepladen medtages ikke. Farver og CaDoodles individuelle materialeindstillinger sendes ikke med. Modellen indsendes til Printværkstedets elev-API med **quality=standard** og **colour=any**. Den eksisterende kø vælger en passende ledig printer og udfører den almindelige slicing. Er printerne optaget eller køen sat på pause, venter jobbet i den varige kø. Printerens kontrol af ledig/ryddet plade er fortsat gældende.

Eleven får en kvittering med jobnummer og status. Kvitteringen skelner mellem klargøring, igangværende print og ventekø. Den er et øjebliksbillede; den løbende status findes i Printværksted. Et afbrudt svar genbruger samme indsendelses-id. Den afventende model og id ligger i `~/.local/state/elevhub/cadoodle-print/pending.json` med sin STL, så en eksplicit gentagelse efter programgenstart heller ikke opretter dobbeltprint. En afventende indsendelse sendes aldrig automatisk ved opstart og omfatter ikke efterfølgende ændringer i tegningen.

Printadressen konfigureres af administrator i `/etc/elevhub/cadoodle-print.json`. På denne VM bruges elevindgangen `https://LOKAL_IP:8770` og skolens CA i `/etc/elevhub/ca.pem`. HTTPS validerer både certifikat og værtsnavn. Ingen lærer- eller printeradgangskoder ligger i CaDoodle.

### Tegning og ekstrudering

- **2D-skitse → 3D**: klik en polygons hjørner, eller træk et rektangel/en ellipse. Luk polygonen ved at klikke første punkt, dobbeltklikke, trykke Enter eller vælge **Luk polygon**.
- **Frihånd → 3D**: tegn med musen. Stregbredden er i millimeter. Overlappende streger samles til en sammenhængende form.
- **Skær hul** fjerner den nye figur fra tegningen. **Viskelæder** fjerner et frit tegnet spor. Huller bevares gennem ekstrudering.
- Højde vælges fra **0,4 til 180 mm**. Stregbredde vælges fra **0,4 til 30 mm**. Komma og punktum accepteres som decimaltegn.
- **Fortryd/Gentag**, Ctrl+Z/Ctrl+Y og valgfri fastgørelse til 1 mm. Esc afbryder den igangværende polygon.
- 3D-visningen opdateres efter en afsluttet figur og ved ændret højde. Den viser bredde, dybde og højde. Træk i visningen for at dreje figuren.
- **Indsæt i CaDoodle** laver et almindeligt objekt i det aktuelle projekt. Det kan flyttes, skaleres, grupperes og eksporteres sammen med andre modeller. CaDoodles fortryd-historik omfatter indsættelsen.
- **Gem skitse/Åbn skitse** bevarer den redigerbare 2D-tegning. Indsættelse gemmer automatisk både `.klosterskitse` og `.stl` i `Projekter/3D-print/Skitser`. CaDoodle kopierer STL'en ind i projektet, så genåbning ikke afhænger af en midlertidig fil.

Indsatte objekter er polygonmodeller. En gemt 2D-skitse kan genåbnes og ændres; en ny indsættelse opretter et nyt objekt. Der er ikke en parametrisk forbindelse tilbage fra et eksisterende 3D-objekt til dets skitse. Skitseværktøjet har heller ikke CAD-begrænsninger som tangent, parallel eller koncentrisk.

## Plade og udtryk

Hovedarbejdsplanet er **180 × 180 mm**, centreret omkring CaDoodles koordinatnul, med millimeternet og blå kant. Størrelsen fremgår af bundlinjen; de tidligere store tal uden for kanten er fjernet. **Vis hele arbejdspladen** (kubeikonet under zoomknapperne) genskaber overblikket. Fastgørelsens trinstørrelse ændrer ikke pladens fysiske størrelse eller millimeternettets mål.

Plade og mål er visuelle elementer i 3D-visningen. De er ikke projektobjekter og kommer aldrig med i STL-eksporten. A1 mini har et fysisk byggevolumen på [180 × 180 × 180 mm](https://us.store.bambulab.com/products/a1-mini). Printværkstedets elevkø har fortsat sin konservative grænse på **175 × 175 × 175 mm**; udvidelsen ændrer ikke denne kontrol.

Temaet følger [Klostermarksskolens hovedskole](https://klostermarksskolen.dk/): Metrophobic, bordeaux `#b4121a`, hvide og lysegrå flader, mørke tekster og diskrete rammer. CaDoodles egne formikoner og programnavn bevares. Metrophobic distribueres med sin OFL-licens i `assets/`.

## Byg på VM'en

Kopiér hele `kloster`-mappen til `/home/elevhub/cadoodle-kloster-build/`. Byg med den officielle JAR som input:

```sh
sudo python3 /home/elevhub/cadoodle-kloster-build/kloster/build.py \
  --jar /home/elev/bin/CaDoodle-ApplicationInstall/0.46.094/CaDoodle-Application.official.jar \
  --jdk /home/elev/bin/CaDoodle-ApplicationInstall/zulu25.36.15-ca-fx-jdk25.0.4-linux_x64 \
  --out /home/elevhub/cadoodle-kloster-build/result
```

Før første installation hedder inputfilen `CaDoodle-Application.jar`. Efter installation findes originalen som `CaDoodle-Application.official.jar`.

Byggeren kontrollerer originalens SHA256, henter tre Java-kildefiler fra fastlåste Git-commits og kompilerer dem og udvidelsen mod den officielle JAR. Afhængighederne hentes ikke igen. Derudover tilpasses CSS og FXML-farver. Output er en ny `CaDoodle-Kloster.jar`; originalen redigeres ikke under bygningen. Dette er en lokal skoleudvidelse, ikke en officiel CaDoodle-udgivelse.

Luk CaDoodle, og installér derefter:

```sh
sudo python3 /home/elevhub/cadoodle-kloster-build/kloster/deploy.py \
  /home/elevhub/cadoodle-kloster-build/result/CaDoodle-Kloster.jar \
  --print-url https://LOKAL_IP:8770 --print-ca /etc/elevhub/ca.pem
```

`deploy.py` nægter at erstatte en åben JAR, gemmer og kontrollerer originalen og udskifter først filen, når det nye arkiv er kontrolleret. Den eksisterende elevlauncher og `pinVersion` bruges fortsat. Start programmet fra **3D-værksted**.

Tilpas printadresse og CA til skolens installation. Ved senere opdateringer kan `--print-url` og `--print-ca` udelades, så den eksisterende konfiguration bevares.

Gendan standardprogrammet med CaDoodle lukket:

```sh
sudo python3 /home/elevhub/cadoodle-kloster-build/kloster/deploy.py --restore
```

Ved en fremtidig CaDoodle-opdatering skal integrationen tilpasses og afprøves igen. Hashkontrollen skal ikke blot fjernes. Projekter og gemte skitser slettes ikke ved afinstallation af udvidelsen.

## Verifikation

Printkøen kan midlertidigt blokere en computer ved mange indsendelser, fælles for CaDoodle og browseren: som standard ved forsøg nr. 10 inden for 60 minutter. Blokeringen udløber efter én time, og læreren kan ophæve den straks. Se [midlertidig blokering, lærerbetjening og identitet](PRINTGRAENSER.md).

Bygningen kører `SketchGeometryTest`: kendte volumenberegninger, lukkede og korrekt orienterede masker, gennemgående huller, indlejrede øer, konkave figurer, adskilte dele, sammenlægning af overlap, krydsende frihåndsstreger, viskelæder, kurver, klipning ved pladekanten og ugyldige højder. Kurver tilnærmes med højst 0,08 mm plan tolerance.

`tests/slice_fixtures.py` kontrollerer tre af de genererede STL-prøver med Printværkstedets rigtige Bambu Studio/A1 mini-profiler. Prøverne er en figur med hul, en krydsende frihåndstegning og samme tegning efter viskelæder. Testen sender ikke noget til en printer.

```powershell
./.venv/Scripts/python.exe linux-elevhub/cadoodle/kloster/tests/slice_fixtures.py `
  output/cadoodle/fixtures --work output/cadoodle/kloster-slice-tests
```

Prøverne sender ingen fysisk maskinstart. Afprøv brugerflade, gemning,
eksport og skolens faktiske printer særskilt før elevernes produktion.

## Guidede projekter

Guidede projekter kan bestå af trin, byggehandlinger, illustrerede tips, kontrolpunkter og bogmærke til genoptagelse. Startere skal bestå af dele, som eleven selv ændrer og samler. Pakken installeres ikke automatisk på elevmaskiner.

Skolens lokale tema og elevprojekter skal bevares ved opdatering. Fysisk og
VM-baseret afprøvning dokumenteres særskilt fra en programbygning.
