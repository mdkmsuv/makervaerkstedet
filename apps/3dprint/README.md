# 3D-værksted

Byg modeller af former, tekst og huller. Værkstedet kan bruges lokalt uden
internet; aflevering til skolens printsystem er en særskilt handling.

**Lav en spillebrik** indsætter fire printdele fra Skoleværkstedets
Brætspilsfabrik som en valgt gruppe med de oprindelige mål. Vælg sokkel,
krop, hoved og hat i egenskabernes rullemenuer. Brikken opdateres straks, og hvert
valg kan fortrydes i ét trin.
Vælg **Del op** ved siden af **Saml** for at flytte eller ændre sokkel, krop, hoved og hat
hver for sig. Delene står klar til print og samles bagefter med et grillspyd.

De 28 dele kommer fra Mathias Kusk Damgaards
[Skoleværkstedet](https://xn--skolevrkstedet-5ib.dk/). De er pakket lokalt som
`web/assets/model3d/game-piece-data.json` med de oprindelige mesh- og samlingsdata.
Kilden `scripts/brik-data.js` havde SHA-256
`f96309ee3622423a443ac713248ec48ca83b5ecf0a22ef6848c6cd09ce65855b`.

Træk **Rundt hul** eller **Kassehul** hen på toppen eller siden af en figur.
Omridset viser, hvor hullet lander. Slip for at placere hullet, og vælg
**Skær hullet ud**, når det ligger rigtigt. **Vis model** indrammer figurerne,
og **Vinkler** lader dig se dem fra en anden side med samme fokus. Brug
**Hele pladen** for at få overblikket tilbage.

Træk i selve figuren for at flytte den. Klik på en hvid firkant for at se
målene på pladen, eller træk i firkanten for at ændre størrelsen med det samme.
Klik på et vist mål for at skrive en præcis størrelse. Målene findes også i
figurens egenskaber.

Vælg en fast figur og tryk **Byg ovenpå** eller **W** for at løfte arbejdsplanet til
dens top. Nye former lander i samme højde. Træk fra biblioteket for at
vælge placering, eller vælg **Læg på arbejdsplan** for at flytte en figur,
der ligger på pladen. Vælg **Slå arbejdsplan fra**, eller tryk **W** igen, for at vende tilbage til pladen.
Et åbnet projekt starter med arbejdsplanet på pladen.

Under **Former** åbner biblioteket på **Grundformer** med 30 former. Markér
en figur for at se dens egne indstillinger. Du kan fx ændre en stjernes
spidser, en kugles runding og en ringbues vinkel. **Byggedele** har 15 valg,
herunder vinkelbeslag, remskive, monteringsplade, tandhjul og spillebrik.
Kortene viser små 3D-billeder af standardformerne.
Hver byggedel har sine egne indstillinger. **Inspiration** er den sidste
kategori og rummer redigerbare moduler til robotter, riddere og drager.
Vælg hver del på sit eget kort. Til en drage kan du bruge **Dragekrop**,
**Dragehoved**, **Dragefod**, **Dragevinge** og **Dragehale**. Til den anden
side kan du holde Skift nede, mens du vælger **Mere → Spejlvend** på en
kopi af vingen. Ret målene i egenskaberne, og print delene adskilt.
Standardmodulerne er skåret uden støtter i deres placering på pladen
med 0,4 mm dyse, 0,20 mm lag og PLA. Vælg **Del op**, hvis du vil
redigere byggestenene i et modul direkte.
Vælg **Hverdagsobjekter** for kop, toiletrulle, mobiltelefon og knap.
Kortene viser målene på
hverdagsobjekterne. Koppen er 90 mm høj og 78 mm i diameter uden hank.
Toiletrullen måler Ø115 × 95 mm med hul Ø42 mm, og mobiltelefonen
måler 72 × 151 × 9 mm.

Under **Connectorer** finder du snap-tappe og snap-huller i to størrelser.
Brug samme størrelse på de dele, der skal passe sammen. Inspirationsdelene
bruger **9 mm**. Træk en snap-tap hen på en flade og brug **Saml** med den
del, der skal bære den. Træk snap-hullet hen på en flade på den anden del
og vælg **Skær hullet ud**. De to fjedrende arme på tappen kan låse i
hullets udvidelse. Tappen og hullet beholder deres fysiske mål, når du
strækker deres samlinger. Printets pasform afhænger af materiale og tolerancer.

Til et bevægeligt led på egen figur kan du bruge **Kugletap Ø9** og
**Kugleskål Ø9**. Sæt fx tappen på en arm og skålen på en krop med
**Saml**. Print de to figurer adskilt. Kugletappen er lavet til at
klikke i skålen, så armen kan drejes og vinkles efter samling.

Vælg **Vinkler → Nedefra** for at se figurernes underside. **Hele pladen**
giver overblikket tilbage.

Indsæt et tandhjul, en møtrik eller en gevindstang, og ret figurens
indstillinger i egenskaberne. Valg i en rullemenu opdaterer figuren med
det samme. Talfelter opdaterer efter en kort pause eller når du går videre.
Du kan fortryde en ændring i ét trin. Indstillingerne vises igen, når du
markerer figuren, og gemmes sammen med projektet.

**Møtrik** laver et metrisk indvendigt gevind fra M4 til M12 med valg af
stigning, højre- eller venstregevind, højde og ekstra spillerum pr. side.
[Metrisk gevindprofil](https://www.iso.org/standard/85107.html) ·
[Gevindmål og stigninger](https://www.bossard.com/-/media/bossard-group/website/documents/technical-resources/en/f-079-en.pdf)

**Gevindstang** laver udvendigt gevind fra M4 til M12. Vælg en længde
fra 5 til 100 mm. Brug samme størrelse, stigning og retning på stang og
møtrik, og indstil spillerummet på møtrikken. Stangen har affasede ender.
Slå **Liggende print** til i gevindstangens egenskaber for at lægge den
på en fladside. Gevindet omkring resten af stangen bevarer sin stigning
og retning. Slå feltet fra for at få den runde, stående stang igen.
[Grundmål for metrisk gevind](https://www.iso.org/standard/85104.html)

**Tandhjul** laver et lige tandhjul med evolventprofil. Vælg modul, antal
tænder, trykvinkel, tandspillerum, tykkelse og akselhul. Hjul, der skal
gribe i hinanden, skal have samme modul og trykvinkel. Ved nominel afstand
er afstanden mellem akslerne summen af delingsdiametrene delt med to.
Pasform efter print afhænger også af printer og materiale.
[KHK’s tandhjulsreference](https://khkgears.net/pdf/2023/spur-gears.pdf)

Eleven vælger klasse, skriver fornavn og giver projektet et navn. Projektet
gemmes derefter automatisk på denne computer. Vent på **Gemt på denne computer**.
Næste gang åbnes arbejdet gennem **Find mit projekt** på samme enhed. Projektet
bevarer de redigerbare figurer, huller og generatorvalg i `.mkv3d`. Under
**Projekt** kan du vælge **Importér 3MF** eller **Hent 3MF**. Du kan også
trække en 3MF-fil ind på pladen. 3MF bevarer modellens mål og delenes
indbyrdes placering. Editorens 3MF-understøttelse omfatter modelgeometri og
grundfarver pr. del. Slicerens printerprofiler og printindstillinger hører
til i sliceren. STL og 3MF er filer til fremstilling.
[3MF-specifikationen](https://3mf.io/spec/)
Fortryd og Gentag virker på tværs af autogemninger i den åbne editor.

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

Efter ændring af standardmodeller eller formkort kan miniaturebillederne
opdateres fra produktroden med `node apps/3dprint/render-form-thumbnails.mjs`
i Node.js 24. Billedfilen og kortenes billedindeks ligger i Git.
