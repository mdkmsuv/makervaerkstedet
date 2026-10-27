# Makerspacet — kort vejledning til læreren

> Offentlig dokumentation: LOKAL_IP er en pladsholder. Brug skolens egne adresser.

## Før lektionen

- Tænd elevcomputerne. De starter selv på elevskærmen; ingen skal logge ind.
- Åbn lærerpanelet i din browser: **https://LOKAL_IP:8780/** og log ind
  med lærerkoden.
- Alle værksteder er åbne fra start. **Internet er lukket**, indtil du åbner det.

## I lektionen

- **Åbn eller luk et værksted** med knappen i lærerpanelet. En lukning varer
  to timer og åbner derefter af sig selv. Du kan åbne før tid.
- **Internet** åbner kun, når du klikker på det, og lukker igen, når serveren
  genstartes. "Åbn alle"/"Luk alle" rører ikke Internet.
- **Skærmpause** fryser alle elevskærme, når du skal have klassens opmærksomhed.
- **Laser:** eleven trykker *Send til læreren* og får et afleveringsnummer.
  Afleveringerne ses på lærerens laserside. Den kan indtil videre kun åbnes
  på selve serveren (http://127.0.0.1:8767/).
- **Folie:** eleven gemmer SVG-filen på en USB-nøgle; der er ingen aflevering.
- **3D-print:** eleven sender modellen til printkøen fra 3D-værkstedet.

## Når noget driller

| Det sker | Gør sådan |
|---|---|
| Elevskærmen viser "Ingen forbindelse" | Serveren kan ikke nås. Så er **alle værksteder og Internet åbne**, og eleverne kan arbejde videre; deres filer gemmes på computeren. Når serveren er tilbage, gælder dine valg igen. |
| Et værksted vil ikke åbne | Tjek i lærerpanelet, at det er åbent. Bed eleven trykke *Til start* og prøve igen. Hjælper det ikke, så genstart computeren. |
| En laseraflevering kom ikke frem | Fik eleven et afleveringsnummer, er den modtaget. Uden nummer var forbindelsen afbrudt: prøv igen, eller gem SVG på USB. |
| En elev har mistet sit projekt | Se under *Find mit projekt* på elevskærmen; tidligere versioner ligger der. Er filen slettet derfra, kræver gendannelse en vellykket, beskyttet backup. Se [backupvejledningen](backup/README.md). |
| En computer skal geninstalleres | Brug USB-stikket med **netop den computers navn** (fx vaerksted-07). Installationen sletter alt på computeren, også elevprojekter. |

## Godt at vide

- **Elevernes projekter ligger kun på den computer, de arbejdede på.** Er en
  computer i stykker, er dens projekter væk. Bed eleverne bruge samme
  computer hver gang, og tage vigtige filer med på USB.
- Laserafleveringer (med elevens navn og klasse) slettes automatisk 30 dage
  efter, de er afsluttet.
- Opdateringer genstarter aldrig en elevskærm, der er i brug. De virker fra
  næste gang computeren tændes.
- Første gang lærerkoden skal vælges, skal det ske fra en browser på selve
  serveren.

Teknisk hjælp: se `LANCERINGSRAPPORT.md` og `SERVER-IP-SKIFT.md`.
