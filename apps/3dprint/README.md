# 3D-værksted

Byg modeller af former, tekst og huller. Værkstedet kan bruges lokalt uden
internet; aflevering til skolens printsystem er en særskilt handling.

**Lav en spillebrik** åbner Skoleværkstedets Brætspilsfabrik i editoren.
Vælg sokkel, krop, hoved og hat, og se den samlede brik. **Sæt på pladen**
indsætter de fire printdele samlet som én model, med de oprindelige mål.
Delene står hver for sig til print og samles bagefter med et grillspyd.

De 28 dele kommer fra Mathias Kusk Damgaards
[Skoleværkstedet](https://xn--skolevrkstedet-5ib.dk/). De er pakket lokalt som
`web/assets/model3d/game-piece-data.json` med de oprindelige mesh- og samlingsdata.
Kilden `scripts/brik-data.js` havde SHA-256
`f96309ee3622423a443ac713248ec48ca83b5ecf0a22ef6848c6cd09ce65855b`.

Eleven vælger klasse, skriver fornavn og giver projektet et navn. Projektet
gemmes derefter automatisk på denne computer. Vent på **Gemt på denne computer**.
Næste gang åbnes arbejdet gennem **Find mit projekt** på samme enhed. Projektet
bevarer de redigerbare figurer og huller i `.mkv3d`; en eksporteret STL er en
fil til fremstilling. Fortryd og Gentag virker på tværs af autogemninger i den
åbne editor.

**Projekter gemmes på denne computer og slettes efter 30 dage uden gemte
ændringer.** En ny gemt ændring starter en ny frist. Åbning eller gentagen
gemning af uændret indhold forlænger ikke fristen. Projekthylden viser
sletedatoen; hent en kopi til eksempelvis USB, hvis arbejdet skal gemmes længere.

Oprydningen udføres af elevcomputerens fælles lokale projekthylde, også når
3D-værkstedet ikke er åbent eller er afinstalleret. Er computeren slukket, sker
oprydningen efter næste opstart. Det kræver ikke skolens server.

Autogem kræver app `1.0.1` og mindst core `2.1.0~20261116.1`.
Ved åbning af en tidligere version er autogem på pause, indtil eleven vælger
Gem projekt eller Gem kopi, så den nyeste arbejdsudgave bevares.

[Installation og tilvalg](../../docs/NY-APP-GUIDE.md) ·
[Lokal lagring og sletteregel](../../system/linux-elevhub/projekter/README.md)
