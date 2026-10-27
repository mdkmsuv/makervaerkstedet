# CaDoodle på Linux-elevcomputeren

> Offentlig dokumentation: LOKAL_IP er en pladsholder. Brug skolens egne adresser.


Klik **3D-værksted** i bjælken på elevens startside. Genvejen skjules inde
i værkstederne, så eleven bruger **Til start**, før et andet værksted vælges.
Det gælder også native CaDoodle og åbne filvælgere. Indgangen samler:

1. **Åbn CaDoodle**: byg med former, giv projektet et navn, og lad programmet gemme ændringer automatisk.
2. **Eksportér → Fast STL → Export** i CaDoodle: vælg `Projekter/3D-print/STL`. CaDoodle lægger eksporten i en undermappe med projektets navn. Vælg den konkrete `.stl`-fil, når den skal sendes.
3. **Åbn Printværksted**: vælg STL-filen, farve og kvalitet, og tryk **Send til print**.

**Til start** går tilbage til elevstartsiden og skjuler CaDoodle, så det åbne projekt bevares. **Åbn CaDoodle** viser det igen. Lærerens 3D-print-valg styrer også den nye indgang. Lærerens aktuelle lukning skjuler programmet, mens serverkontakten virker. Ved netudfald åbnes CaDoodle og de øvrige lokale værksteder; ved genforbindelse gælder lærerens aktuelle valg igen. Dette er kioskbetjening; det er ikke en generel sikkerhedslås mod lokale programmer.

En lille startopgave står i indgangen: tegn en navnebrik på cirka 50 × 20 × 3 mm. Brug millimeter. Modellen skal passe under Printværkstedets indledende grænse på 175 × 175 × 175 mm.

## Filer og installation

- Elevprojekter ligger fysisk i `/home/elev/Projekter/3D-print/CaDoodle`.
- Eksporterede filer ligger i `/home/elev/Projekter/3D-print/STL`.
- CaDoodles oprindelige projektsti under `Documents/CaDoodle-workspace` peger på projektmappen. Programmets cache ligger fortsat i Documents.
- Officiel Debian-pakke: **1.9.6**, med den medfølgende programversion **0.46.094**. Danske tekster og grundtilstand er aktive. Pakken er hentet fra [projektets officielle udgivelse](https://github.com/CommonWealthRobotics/CaDoodle/releases/tag/1.9.6). Se installation.json (lokal fil: `installation.json`) for URL, størrelse og kontrolleret SHA256.
- `pinVersion` vælger den medfølgende version uden løbende opdateringsdialoger. Grundlæggende modellering og STL-eksport er prøvet med elevens Internet lukket. Ekstra integrationer til Blender, FreeCAD og Inkscape er ikke installeret eller afprøvet.
- Launcher: `/usr/local/bin/elevhub-cadoodle`. Den vælger dansk, tillader Mesa-softwaregrafik og begrænser hver Java-proces til 1536 MiB heap.
- Afprøv skærmopløsning og skalering på det konkrete udstyr, så alle knapper og eksportdialogen er tilgængelige.
- Log ved normal opstart: `/home/elev/.local/state/elevhub/cadoodle.log`.

Den lokale udvidelse tilføjer **2D-skitse → 3D**, **Frihånd → 3D** og en
**180 × 180 mm A1 mini-referenceplade**. Se
[brug, kildekode og tilbageføring](kloster/README.md). Det lokale skoletema
bevares ved opdatering; den offentlige udgave bruger et generisk standardtema.

Kontrollér dansk brugerflade, projektgemning, genåbning og STL-eksport på den
konkrete elevcomputer. En simuleret printkø dokumenterer ikke fysisk print.
Softwaregrafik i en VM kan gøre store modeller langsomme.

## Geninstallation og videre vedligeholdelse

`installer.sh` er en opskrift til den samme Debian-kiosk. Kopiér den sammen med `elevhub-cadoodle`, `klargoer.py` og `ansible/roles/kiosk/files/cadoodle_vaerksted.py` til en installationsmappe på Linux. Kør `sudo sh installer.sh`. Første opstart skal ske som `elev`; kør derefter `sudo python3 klargoer.py INSTALLATIONSMAPPE` igen for at placere de nu oprettede projekter under Projekter. Genstart elevbjælken for at aktivere indgangen.

Den almindelige Ansible-kiosk kopierer nu integrationens Python-modul og kroge. Selve CaDoodle-pakken udrulles fortsat særskilt med installationsopskriften. Kontrollér den faktisk installerede version på hver elevcomputer.
