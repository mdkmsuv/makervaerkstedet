package com.commonwealthrobotics.kloster;

import java.util.List;

/**
 * Aldersneutrale projektstartere til læringsspor i CaDoodle.
 *
 * <p>Starterne er forslag til en arbejdsvej. De er ikke adgangsniveauer, og
 * eleven kan altid vælge en anden starter eller åbne flere værktøjer.</p>
 */
public final class LearningCatalog {
    private LearningCatalog() {
    }

    public record Entry(
            String id,
            String title,
            int stage,
            String summary,
            String gets,
            List<String> tasks,
            String practice,
            String challenge,
            List<String> help,
            String check,
            List<String> tools,
            boolean hasText) {
        public Entry {
            if (id == null || id.isBlank()) {
                throw new IllegalArgumentException("En starter skal have et id");
            }
            if (title == null || title.isBlank()) {
                throw new IllegalArgumentException("En starter skal have en titel");
            }
            if (stage < 1 || stage > 4) {
                throw new IllegalArgumentException("Trin skal være mellem 1 og 4");
            }
            tasks = List.copyOf(tasks);
            help = List.copyOf(help);
            tools = List.copyOf(tools);
        }
    }

    private static final List<Entry> ENTRIES = List.of(
            new Entry(
                    "name-tag",
                    "Navneskilt",
                    1,
                    "Et lille skilt med hul, navn og en hævet tekst.",
                    "Du får en løs plade, en solid cylinder og en tekst, som du bygger til et skilt med hul og relief.",
                    List.of(
                            "Skriv dit navn i tekstfeltet, når du starter projektet.",
                            "Vælg teksten, og ændr størrelse og placering, så den kan være på skiltet.",
                            "Drej modellen, og forklar, hvad der bliver skåret og hævet."),
                    "Du øver tekst, mål og forskellen på en grundform og en tekst.",
                    "Lav en kort undertitel, eller flyt teksten så den stadig holder afstand til hullet.",
                    List.of(
                            "Skriv teksten i tekstfeltet, når projektet starter.",
                            "Klik på teksten for at vælge den.",
                            "Træk i teksten for at flytte den, og brug målene til at ændre størrelsen.",
                            "Drej modellen, og kontrollér afstand til kant og hul."),
                    "Kontrollér, at tekst og hul ligger inden for skiltet. Læreren godkender materialet og den fysiske produktion.",
                    List.of(),
                    true),
            new Entry(
                    "game-piece",
                    "Spillebrik",
                    1,
                    "En stabil spillebrik med bund, skaft og hoved.",
                    "Du får en løs base, et skaft og et hoved, som du bygger til en stabil brik.",
                    List.of(
                            "Vælg farve eller form for hovedet.",
                            "Flyt delene, så brikken står samlet og stabilt.",
                            "Forklar, hvilken del der gør bunden stabil."),
                    "Du øver at genkende dele og deres funktion i et samlet produkt.",
                    "Lav to brikker, der hører sammen, men har hver sin tydelige form.",
                    List.of(
                            "Klik på en del for at vælge den.",
                            "Træk den valgte del til en ny placering.",
                            "Drej modellen for at undersøge, om delene overlapper rigtigt.",
                            "Spørg en makker, om brikken ser stabil ud."),
                    "Kontrollér, at bunden står på arbejdsfladen, og at delene er placeret som én samlet brik. Fysisk stabilitet vurderes med læreren.",
                    List.of(),
                    false),
            new Entry(
                    "desk-sign",
                    "Bordskilt",
                    2,
                    "Et fladt bordskilt med hævet tekst og dele, du selv placerer.",
                    "Du får en løs plade, kanter og tekst, som du løfter, justerer og samler til et læsbart skilt.",
                    List.of(
                            "Arrangér plade og tekst, så skiltet er let at læse.",
                            "Centrér teksten, eller vælg en anden tydelig placering.",
                            "Begrund ét valg ud fra den person eller det sted, der skal bruge skiltet."),
                    "Du øver placering, centrering og sammenhæng mellem bruger og produkt.",
                    "Lav to udgaver til forskellige afstande, og test hvilken der er lettest at læse.",
                    List.of(
                            "Vælg en del med den indbyggede markering.",
                            "Brug Justér til at centrere de valgte dele.",
                            "Løft tekst og kanter, så deres bund går en smule ned i pladens overside.",
                            "Brug Gruppér, når dele skal flyttes sammen.",
                            "Drej modellen, og få en anden til at læse skiltet."),
                    "Kontrollér læsbarhed og afstand til kanten ved at dreje modellen. Fysisk anvendelse afprøves med lærerens mål.",
                    List.of("alignButton", "groupButton"),
                    true),
            new Entry(
                    "robot",
                    "Byg en robot",
                    2,
                    "Sammensæt en robot af navngivne dele, som du selv giver størrelse og placering.",
                    "Du får løse navngivne dele, som du løfter, kopierer og samler til en robot med en valgt funktion.",
                    List.of(
                            "Arrangér delene, så robotten har et tydeligt hoved, krop og bund.",
                            "Giv mindst én del en ny størrelse eller placering.",
                            "Forbind delene til den ønskede samlede form, og forklar dit vigtigste valg."),
                    "Du øver at kopiere, ændre størrelse, justere og gruppere flere dele.",
                    "Lav en robot til en bestemt opgave, og ændr formen efter en makkers forslag.",
                    List.of(
                            "Klik på en del for at se, hvad der er valgt.",
                            "Brug Kopiér og derefter Indsæt, hvis to dele skal ligne hinanden.",
                            "Brug Justér til at rette placeringen.",
                            "Løft krop og hoved, så delene overlapper lidt ved samlingerne.",
                            "Brug Gruppér, når delene skal flyttes som én samlet figur."),
                    "Kontrollér, at delene hænger sammen som den ønskede enkeltstående form, og at der ikke er utilsigtede overlap. Læreren godkender, om den kan fremstilles.",
                    List.of("copyButton", "pasteButton", "alignButton", "groupButton"),
                    false),
            new Entry(
                    "pencil-holder",
                    "Blyantholder",
                    3,
                    "Byg en blyantholder med to rum, og tilpas rummene til forskelligt indhold.",
                    "Du får en massiv blok på 60 × 35 × 35 mm og én løs skæreblok. Du skaber selv åbningerne.",
                    List.of(
                            "Ændr højderne og placér skæreblokken med bund og vægge.",
                            "Lav et hul, kopiér det og gruppér til en holder med to rum.",
                            "Gør rummene forskellige, så de passer til det, du vil opbevare."),
                    "Du øver at skelne mellem solid form og hul samt at gruppere og opdele dele.",
                    "Tilpas holderen til et bestemt antal blyanter, og sammenlign din idé med en anden løsning.",
                    List.of(
                            "Vælg den samlede holder med den indbyggede markering.",
                            "Brug Opdel for at inspicere delene enkeltvis.",
                            "Ændr den markerede del, og drej modellen for at se virkningen.",
                            "Gruppér delene igen, når formen er klar til kontrol."),
                    "Kontrollér, at hullet er åbent, og at den ønskede yderform stadig er solid. Pasform kræver fysisk afprøvning med lærerens mål.",
                    List.of("ungroupButton", "groupButton"),
                    false),
            new Entry(
                    "open-box",
                    "Åben æske",
                    4,
                    "Design en åben æske til et bestemt indhold og forbedr den efter en fysisk prøve.",
                    "Du får en solid yderform og en solid indre form, som du målretter, løfter, gør til hul og samler.",
                    List.of(
                            "Mål det indhold, æsken skal rumme, og skriv dine kriterier ned.",
                            "Tilpas æskens mål til indholdet, og vælg en tydelig detalje til designet.",
                            "Lav en prøve, test plads og samling, og forbedr én målsat del."),
                    "Du øver at arbejde med mål, brugerkrav og forbedringer fra model til fysisk produkt.",
                    "Sammenlign to løsninger og begrund den, der bruger materialet bedst til dit formål.",
                    List.of(
                            "Brug arbejdsfladen og målene til at kontrollere størrelsen.",
                            "Vælg en del, før du ændrer den.",
                            "Drej modellen for at finde overlap eller dele uden for området.",
                            "Notér, hvad den fysiske prøve viser, før du forbedrer modellen."),
                    "Kontrollér mål, indholdsområde og samlinger. Mål og pasform skal verificeres fysisk med lærerens mål.",
                    List.of("workplaneButton"),
                    false));

    public static List<Entry> entries() {
        return ENTRIES;
    }

    public static Entry find(String id) {
        return ENTRIES.stream()
                .filter(entry -> entry.id().equals(id))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException("Ukendt starter: " + id));
    }

    public static String stageTitle(int stage) {
        return switch (stage) {
            case 1 -> "Gør den til din";
            case 2 -> "Byg med former";
            case 3 -> "Form og kombinér";
            case 4 -> "Design, afprøv, forbedr";
            default -> throw new IllegalArgumentException("Ukendt trin: " + stage);
        };
    }
}
