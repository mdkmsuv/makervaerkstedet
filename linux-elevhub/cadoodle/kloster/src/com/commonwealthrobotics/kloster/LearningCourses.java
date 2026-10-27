package com.commonwealthrobotics.kloster;

import java.util.List;
import java.util.Map;

/** Konkrete byggeveje med kontrolpunkt i hvert trin. */
public final class LearningCourses {
    private static final List<String> ILLUSTRATIONS = List.of("view", "move", "size", "lift", "copy", "hole", "align", "group", "finish");
    private static final String SELECT = "Klik på én del. Hold Shift nede, og klik på flere dele for at vælge dem sammen. Klik på et tomt sted for at ophæve markeringen.";
    private static final String SIZE = "Vælg kun den del, du vil ændre. Klik på dens måltal, skriv det nye mål, og tryk Enter. Ændr én retning ad gangen; komma kan bruges i decimaltal.";
    private static final String LIFT = "Vælg delen og brug løftehåndtaget over figuren. Indstil afstanden fra arbejdsplanet til delens bund; dette er ikke figurens højde. Drej med højre museknap for at kontrollere fra siden.";

    public record Step(String title, String instruction, String check, String hint, List<String> tools, String illustration) {
        public Step {
            if (title == null || title.isBlank() || instruction == null || instruction.isBlank() || check == null || check.isBlank() || hint == null || hint.isBlank()) throw new IllegalArgumentException("Ufuldstændigt guidetrin");
            if (!ILLUSTRATIONS.contains(illustration)) throw new IllegalArgumentException("Ukendt illustration: " + illustration);
            tools = List.copyOf(tools);
        }
    }
    private static Step s(String t, String i, String c, String h, String v, String... tools) { return new Step(t, i, c, h, List.of(tools), v); }

    private static final Map<String, List<Step>> COURSES = Map.of(
        "name-tag", List.of(
            s("Se og vælg", "Du har tre løse dele: plade, cylinder og din tekst. Hold højre museknap nede og træk for at dreje. Klik derefter på pladen.", "Er kun pladen markeret?", SELECT + " Zoom med scroll, eller brug to fingre på skærmen.", "view", "fitViewButton"),
            s("Gør pladen bredere", "Ændr pladens bredde fra 60 til 65 mm. Behold dybden 25 mm og højden 3 mm.", "Viser pladens tre mål 65 × 25 × 3 mm?", SIZE, "size"),
            s("Placér ophænget", "Flyt den løse cylinder ind på pladens venstre ende. Den er 6,4 mm bred og 4 mm høj. Behold mindst 3 mm fra cylinderens side til pladens kanter.", "Går cylinderen fra arbejdsplanet og op gennem pladens overside?", "Klik på cylinderen, og træk den vandret. Klik TOP på visningskuben for at se kantafstandene. Lad tekst og cylinder være adskilt.", "move"),
            s("Skær et hul", "Vælg kun cylinderen og tryk Hul. Hold Shift, vælg også pladen, og tryk Gruppér.", "Kan du se arbejdsplanet gennem et rundt hul i pladen?", SELECT + " Hul-formen fjerner materiale dér, hvor den overlapper pladen, når du grupperer.", "hole", "holeButton", "groupButton"),
            s("Giv teksten mål", "Vælg den løse tekst, du skrev ved start. Giv den bredden 35 mm, dybden 8 mm og højden 1,2 mm.", "Er teksten mindre end pladen og stadig læsbar?", SIZE + " Du ændrer den eksisterende tekstform; du skal ikke indsætte en ny.", "size"),
            s("Løft teksten på plads", "Flyt teksten ind på pladen, fri af hullet. Løft den, så dens bund er 2,7 mm OVER arbejdsplanet. Den går nu 0,3 mm ned i pladens overside.", "Er bogstaverne hævet over pladen og forbundet med den?", LIFT, "lift"),
            s("Saml skiltet", "Markér tekst og plade med Shift, og tryk Gruppér. Flyt skiltet lidt for at se, at det hænger sammen.", "Følger bogstaverne med, og er hullet stadig åbent?", SELECT + " Fortryd flytningen, hvis du vil have skiltet tilbage på samme sted.", "group", "groupButton"),
            s("Lav dit eget kendetegn", "Vælg en lille figur i figurpanelet, klik på arbejdsplanet, og ændr dens mål. Placér og løft den på skiltet med lidt overlap. Gruppér den med skiltet.", "Kan din figur hjælpe en bruger med at genkende skiltet uden at dække navn eller hul?", "Prøv fx en 6 × 6 × 1 mm figur med bund 2,7 mm over arbejdsplanet. Vælg selv form og placering.", "move", "groupButton"),
            s("Slutkontrol", "Kontrollér mål, tekst, hul, kantafstand og arbejdsområde; vælg selv én ændring til version 2.", "Hvad skal læreren kontrollere fysisk?", "Materiale og fremstilling kræver lærerens kontrol.", "finish", "fitViewButton")),
        "game-piece", List.of(
            s("Find base", "Drej og klik på den løse base; forklar hvilken flade der skal stå på arbejdsplanet.", "Kan du vise undersiden?", "Brug Vis hele modellen ved behov.", "view", "fitViewButton"),
            s("Base 32 mm", "Ændr basens bredde og dybde til 32 mm. Behold højden 3 mm og bunden på arbejdsplanet.", "Viser målene 32 × 32 × 3 mm?", SIZE, "size"),
            s("Stilk 20 mm", "Vælg skaftet, og sæt højden til 20 mm. Behold bredden og dybden 10 mm.", "Er skaftet smallere end basen?", SIZE, "size"),
            s("Justér og løft", "Vælg base og skaft med Shift. Tryk Justér og vælg midten i de to vandrette retninger. Vælg derefter kun skaftet og sæt bundens afstand til 2,5 mm.", "Er skaftet centreret med 0,5 mm overlap i den 3 mm høje base?", "Brug de vandrette midtermarkører i Justér; undlad den lodrette. " + LIFT, "align", "alignButton"),
            s("Form og placér hovedet", "Giv hovedet målene 14 × 14 × 14 mm. Centrér det over skaftet i de to vandrette retninger med Justér. Vælg kun hovedet, og løft bunden til 22,2 mm.", "Overlapper hovedet skaftets top ved 22,5 mm?", SIZE + " " + LIFT, "lift", "alignButton"),
            s("Gruppér", "Markér base, skaft og hoved, og gruppér dem.", "Flytter hele brikken samlet?", "Kontrollér alle samlinger først.", "group", "groupButton"),
            s("Kopiér hel brik", "Markér hele brikken, kopiér/indsæt, og flyt kopien til siden.", "Er kopien en hel brik?", "Markér gruppen, ikke kun hovedet.", "copy", "copyButton", "pasteButton"),
            s("Opdel og ændr", "Vælg kopien og tryk Opdel. Gør dens hoved 18 mm bredt, behold de andre mål, og centrér det igen. Gruppér kopiens tre dele.", "Har du to sammenhængende brikker med forskellig silhuet?", "Brug Shift til kun at vælge kopiens dele. Kontrollér overlap efter ændringen.", "size", "ungroupButton", "alignButton", "groupButton"),
            s("Kontrol og valg", "Kontrollér base, samlinger og overlap; vælg selv en regel for et nyt briksæt.", "Hvad kræver fysisk stabilitetskontrol?", "Stabilitet afgøres ved fysisk prøve.", "finish", "fitViewButton")),
        "robot", List.of(
            s("Se delene", "Find sokkel, krop, hoved og to arme. Alle fem dele står hver for sig på arbejdsplanet.", "Kan du markere hver del alene?", SELECT, "view"),
            s("Giv kroppen højde", "Ændr kroppens højde fra 19 til 22 mm. Placér den midt på soklen; lad soklen blive på arbejdsplanet.", "Har du ændret kroppens mål og placering?", SIZE + " Markér krop og sokkel med Shift og brug de to vandrette midtermarkører i Justér.", "size", "alignButton"),
            s("Løft krop og hoved", "Sæt kroppens bund til 2,5 mm over arbejdsplanet. Centrér hovedet over kroppen og sæt hovedets bund til 24,2 mm.", "Overlapper kroppen soklen, og overlapper hovedet kroppens top ved 24,5 mm?", LIFT + " Brug Justér vandret; vælg kun én del, når du løfter.", "lift", "alignButton"),
            s("Form armene", "Gør hver arm 16 mm høj. Flyt dem til hver sin side af kroppen med 1 mm overlap ind i kroppen. Løft begge armes bund til 7 mm.", "Hænger begge arme fast i kroppen fra både top- og sidevisning?", SIZE + " " + LIFT, "lift"),
            s("Saml", "Markér den delkonstruktion der skal flyttes sammen, og gruppér den.", "Flytter den samlet?", "Gruppér efter højde- og sidekontrol.", "group", "groupButton"),
            s("Giv robotten en opgave", "Tryk Opdel. Vælg en robotopgave, og ændr mindst to dele: fx brede skubbearme og en lav krop. Saml robotten igen med overlap og Gruppér.", "Kan en makker gætte opgaven ud fra formen?", "Vælg selv mål. Vil du genbruge en del, så vælg den, tryk Kopiér, derefter Indsæt, og flyt kopien.", "size", "ungroupButton", "copyButton", "pasteButton", "groupButton"),
            s("Forbedr", "Brug makkerens gæt til én ændring, og behold den fungerende del.", "Kan du vise feedbackens spor?", "Notér én observation først.", "move"),
            s("Slutkontrol", "Kontrollér sokkel på arbejdsplanet, løft, samlinger og område; vælg næste funktion.", "Hvilken samling kræver fysisk kontrol?", "Digital visning beviser ikke styrke.", "finish", "fitViewButton")),
        "desk-sign", List.of(
            s("Se dele", "Klik på plade, kanter og tekst, og drej for at se flad og hævet del.", "Kan du pege på alle løse dele?", "Vælg én del ad gangen.", "view"),
            s("Vælg en læser", "Vælg et sted til skiltet. Behold pladens 70 × 25 × 3 mm, og ændr den løse teksts bredde til 40 mm.", "Passer teksten til den person eller det sted, du valgte ved start?", SIZE + " Tekstens ord er allerede lavet ud fra dit tekstfelt ved start.", "size"),
            s("Lav en hævet ramme", "Flyt de fire kantstykker ind på pladen, så de danner en ramme uden huller i hjørnerne. Løft hvert stykkes bund til 2,7 mm.", "Er rammen hævet og forbundet med pladen?", LIFT + " De lange stykker ligger langs pladens lange sider; de korte lukker enderne. Lad hjørnerne overlappe lidt.", "lift"),
            s("Løft teksten", "Placér teksten inden for rammen. Løft tekstens bund til 2,7 mm, så den går 0,3 mm ned i pladen.", "Er teksten hævet og fri af rammen?", LIFT, "lift"),
            s("Justér XY", "Markér plade og tekst, og brug Justér til X/Y-centrering; kontrollér Z bagefter.", "Er tekst centreret uden at blive flad?", "Justér må ikke erstatte Z-løft.", "align", "alignButton"),
            s("Gruppér", "Markér plade, kanter og tekst, og gruppér efter højde- og læsekontrol.", "Flytter skiltet samlet med relief?", "Gruppér til sidst i byggevejen.", "group", "groupButton"),
            s("Gør læsningen bedre", "Lad en makker læse skiltet på skærmen. Tryk Opdel, ændr tekstens bredde eller placering efter feedback, og gruppér igen.", "Er teksten stadig inden for rammen og forbundet med pladen?", "Behold tekstens bund på 2,7 mm. Den fysiske læseafstand må afprøves, når der findes et print eller en papirprøve.", "size", "ungroupButton", "groupButton"),
            s("Slutkontrol", "Kontrollér løft, tekst, kanter og område; vælg ny bruger eller afstand til version 2.", "Hvad skal afprøves fysisk?", "Lærerens mål afgør anvendelse.", "finish", "fitViewButton")),
        "pencil-holder", List.of(
            s("Find de to blokke", "Du har en stor massiv blok og én mindre skæreblok. Den lille skal fjerne materiale fra den store. Der er endnu ingen åbning.", "Kan du vælge hver blok alene?", SELECT, "view"),
            s("Gør holderen højere", "Ændr den store bloks højde fra 35 til 40 mm. Behold bredden 60 mm og dybden 35 mm.", "Viser målene 60 × 35 × 40 mm?", SIZE, "size"),
            s("Giv skæreblokken mål", "Vælg den lille blok. Sæt målene til 25,5 × 29 × 40 mm.", "Er den smallere end den store blok og lige så høj?", SIZE, "size"),
            s("Bevar bund og vægge", "Flyt den lille blok ind i den store med 3 mm afstand fra venstre, forreste og bageste kant. Løft dens bund til 3 mm over arbejdsplanet.", "Rager skæreblokken 3 mm over toppen, mens der er 3 mm materiale under den?", "Brug TOP-visningen og gitteret til kantafstandene. " + LIFT + " Den store blok skal blive på arbejdsplanet.", "move"),
            s("Kopiér det andet rum", "Vælg skæreblokken og tryk Hul, så Kopiér og Indsæt. Flyt kopien 28,5 mm til højre i X-retningen. Behold samme dybdeplacering og bundhøjde.", "Har du to hulformer med 3 mm mellemrum og 3 mm til yderkanterne?", "Sæt Snap-gitter til 0,5 mm i bundlinjen. Brug gitter/lineal til forskydningen, og kontrollér ovenfra. Åbningerne kommer først, når du grupperer.", "hole", "holeButton", "copyButton", "pasteButton"),
            s("Gruppér holder", "Markér yderform og begge huller, og gruppér.", "Er begge rum åbne og yderformen solid?", "Kontrollér toppen først.", "group", "groupButton"),
            s("Tilpas to rum", "Tryk Opdel. Gør venstre hul 22,5 mm bredt og højre hul 28,5 mm bredt. Flyt dem, så ydervæggene og skillevæggen stadig er 3 mm.", "Har du lavet et smalt og et bredt rum, der passer til to forskellige ting?", "Bredderne giver stadig 22,5 + 28,5 + 3 + 3 + 3 = 60 mm. Behold dybde 29 mm, højde 40 mm og bundhøjde 3 mm.", "size", "ungroupButton"),
            s("Saml igen", "Gruppér valgt løsning, og kontrollér bund, vægge og to åbne rum.", "Kan du vise alle tre?", "Drej hele modellen efter gruppering.", "group", "groupButton"),
            s("Næste prøve", "Vælg et nyt indholdsmål eller en papirprøve før eventuelt lærerprint.", "Hvilket mål er usikkert?", "Pasform kræver lærerens fysiske kontrol.", "finish", "fitViewButton")),
        "open-box", List.of(
            s("Mål indhold", "Vælg indhold og skriv bredde, dybde og højde.", "Kan målene sammenholdes med det indre rum?", "Mål samme retning.", "view"),
            s("Sæt ydermål", "Vælg indvendig bredde og dybde ud fra indholdets mål plus 2 mm spillerum. Sæt yderblokkens bredde og dybde til disse mål plus 6 mm; vælg en passende højde.", "Er der regnet 3 mm væg med på hver side?", SIZE + " Eksempel: et indre rum på 50 × 30 mm kræver 56 × 36 mm udvendigt. Start med højde 20 mm.", "size"),
            s("Sæt hullets mål", "Giv den lille blok de valgte indvendige mål. Dens højde skal være mindst lige så stor som yderblokkens højde, så den kan skære toppen væk.", "Er skæreblokken mindre i bredde og dybde, men høj nok til at gå igennem toppen?", SIZE + " I eksemplet bliver skæreblokken 50 × 30 × 20 mm. Den er endnu solid.", "size"),
            s("Justér og løft", "Justér yderform og cutter i X/Y, og løft cutter så bunden er cirka 3 mm.", "Er cutter centreret med bund og vægge?", "Justér ikke Z; løft bagefter.", "align", "alignButton"),
            s("Skær æsken åben", "Vælg den indre blok og tryk Hul. Vælg også yderblokken med Shift, og tryk Gruppér.", "Er æsken åben med bund og vægge tilbage?", SELECT + " Hvis toppen stadig er lukket: Fortryd, og kontrollér skæreblokkens højde og løft.", "hole", "holeButton", "groupButton"),
            s("Lav din egen inddeling", "Tilføj en terning fra figurpanelet. Gør den 3 mm bred, så dyb som æskens indvendige rum plus 0,6 mm og lidt lavere end æskens kant. Flyt den ind som skillevæg, og løft bunden til 2,7 mm.", "Går skillevæggen lidt ned i bunden og ind i de to sidevægge?", "Vælg placeringen efter indholdet. I eksemplet kan væggen være 3 × 30,6 × 14 mm. Kontrollér fra top og side.", "size"),
            s("Saml valgt", "Gruppér valgt version, og kontrollér at rummet stadig er åbent.", "Flytter den samlet uden tab af vægge?", "Kontrollér ovenfra og fra siden.", "group", "groupButton"),
            s("Papirprøve", "Mål en papir- eller papprøve, og notér én ændring før eventuelt lærerprint.", "Hvilken måling bekræfter løsningen?", "Lærer godkender fysisk fremstilling.", "view"),
            s("Næste iteration", "Kontrollér mål, bund, vægge og område; vælg næste brugerkrav.", "Kan du skelne færdigt valg fra næste spørgsmål?", "Pasform og materiale afgøres ikke alene digitalt.", "finish", "fitViewButton")));

    private LearningCourses() { }
    public static List<Step> steps(String id) {
        List<Step> result = COURSES.get(id);
        if (result == null) throw new IllegalArgumentException("Ukendt guidet forløb: " + id);
        return result;
    }
}
