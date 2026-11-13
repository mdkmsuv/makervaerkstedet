# UI/UX-krav til Makerværkstedet

Aftalt målgruppe 11. november 2026: 2.–10. klasse i dansk folkeskole samt
voksne med henholdsvis gode og begrænsede IT-færdigheder. Brug fælles krav
og relevante målgruppeafsnit ved alle UI-/flowændringer.

Målet er forståelige og gennemførlige opgaver med passende hjælp. Et flot
skærmbillede eller en grøn automatisk test er ikke tilstrækkeligt. Kravene
er projektvalg, medmindre et WCAG-kriterium udtrykkeligt angives.
Editorens konkrete arbejdsform bygger især på Excalidraw, Penpot, tldraw,
GDevelop, Fluent 2 og Material 3. WCAG er tilgængelighedsgrundlaget, ikke
hele definitionen af god UI/UX. WCAG 2.2 AA er målet; listen er et relevant udvalg,
ikke en fuld overensstemmelseskontrol eller juridisk vurdering.
Se [research og kildebegrænsninger](UI-UX-KILDER-20261111.md).

## Målgrupper — praktiske profiler

| Profil | Designhensyn | Relevant prøve |
|---|---|---|
| 2.–3. klasse | Korte konkrete instruktioner ved handlingen, genkendelige billeder sammen med ord, overskuelig start og synlig vej til nødvendig voksenhjælp. | Finder eleven første handling og næste skridt med den støtte, der faktisk findes? |
| 4.–6. klasse | Tydelig arbejdsgang, eksempler og hjælp efter behov; plads til at eksperimentere og fortryde. | Kan eleven gennemføre kerneopgaven og rette en almindelig fejl? |
| 7.–10. klasse | Respektfuldt udtryk, præcision og flere muligheder uden at overfylde første møde. | Kan både ny og rutineret elev arbejde uden unødige hjælpetvangstrin? |
| Voksne med begrænset IT-erfaring | Almindeligt dansk, navngivne handlinger, stabile placeringer, tydelig status og hjælp ved opgaven. | Kan opgaven udføres uden kendskab til porte, filstrukturer og systemarkitektur? |
| IT-vante voksne | Effektive gentagne handlinger, relevante detaljer og synlige genveje med samme tydelige status og rettigheder. | Kan rutineopgaven gentages uden overflødige trin og med forståelige konsekvenser? |

Profilerne er arbejdsinddeling, ikke videnskabelige grænser for evner.
Alder er ikke lig læseniveau, digital erfaring eller motorik. Tilpas efter
opgave og faktiske brugere; indfør ikke nye konti, elevdata eller tracking
for at gætte evner. Hjælp kræver ikke en ydmygende “dårlig til IT”-tilstand.

## Fælles, efterprøvelige krav

| ID | Krav | Review |
|---|---|---|
| UX-01 Formål | Sidetitel og tydelig primær handling forklarer opgaven. | Start fra den faktiske indgang; systemarkitekturen skal ikke forklares for at komme videre. |
| UX-02 Arbejdsgang | Nødvendige valg først, ekstra indstillinger efter behov. | Ingen overflødige skærme eller tekniske omveje; tilbage/redigering bevarer gyldige valg. |
| UX-03 Navigation | Genkendelige labels, stabile placeringer og tydelig aktuel placering. | Links åbner rigtig installation og funktion; generel administration erstatter ikke en ønsket direkte handling. |
| UX-04 Sprog | Korte konkrete og respektfulde tekster; synlig tekstlabel på central funktion. | “Send til printkø” beskriver køaflevering; kernehandling bæres ikke af ikon/tooltip alene. |
| UX-05 Hjælp | Hjælp står ved beslutningen; eksempel, tomtilstand og første skridt er forståelige. | Ny bruger kan starte, og øvet bruger kan fortsætte uden tvungen tutorial hver gang. |
| UX-06 Status | Skeln mellem sender, modtaget, i kø, udføres, færdig og ukendt efter datagrundlaget. | Serverkvittering er ikke færdigt fysisk arbejde. Gammel/offline status markeres; vis tidspunkt, når det findes. |
| UX-07 Fejl | Forklar hændelse, betydning og næste mulige handling. | Gyldigt input bevares; netværksfejl kaldes ikke forkert kode. Tekniske detaljer kan åbnes særskilt. |
| UX-08 Kontrol | Reversible fejl kan fortrydes/rettes; konsekvensfulde handlinger er præcise. | Målmaskine/job fremgår. Ingen dialog ved ethvert trivielt klik eller blind genafsendelse ved ukendt jobudfald. |
| UX-09 Visuelt system | Genbrug typografi, afstande, farver, ikoner og komponenttilstande. | Klar visuel rangorden; ingen ny palette eller knapvariant uden formål. Privat skoleprofil holdes uden for generisk produkt. |
| UX-10 Layout | Kerneopgaven fungerer på aftalte skærme og ved relevant zoom. | Ingen skjulte handlinger, afklippede labels eller overlappende dialoger. Dokumentér skærm/zoom ved nye layouts. |
| UX-11 Rolle/adgang | Elev-/lærergrænser er tydelige og ens i UI og backend. | Aftalt offentlig elevfunktion kræver ikke lærerlogin. Lærerindgang er synlig, lærerhandling er beskyttet. |
| UX-12 Sprogvalg | Dansk er sammenhængende; understøttet English har samme opgavebetydning. | Ingen rå tekniske fejltekster. Installer følger aftalt OS-/skolesprog med forståelig mulighed for rettelse. |

Bevar fungerende flows og skoleprofil, medmindre opgaven ændrer dem.
Ældre planer låser ikke fejlbehæftede layouts fast.

## Præcise editorregler

Disse er projektkrav udledt af aktuelle editorer og designsystemer, ikke
en ordre om at tilføje alle funktioner til hver side. De gælder, hvor funktionen
findes eller indgår i bestillingen. En manglende funktion registreres som et
begrundet forbedringsforslag, hvis den ligger uden for opgaven.

| ID | Mønster | Konkret accept |
|---|---|---|
| ED-01 Arbejdsflade | Adskil global navigation, dokumenthandlinger, værktøjskasse og egenskaber. Canvas er hovedområdet. | Åbning af hjælp/egenskaber mister ikke dokument eller markering. Nødvendige værktøjer flytter ikke vilkårligt. |
| ED-02 Markering | Aktivt værktøj, markeret objekt og tastaturfokus kan skelnes. | Vælg objekt A og derefter B: panelets titel/værdier følger B. Ingen egenskab ændrer det tidligere objekt ved et uheld. |
| ED-03 Relevante egenskaber | Vis kontroller til den valgte objekttype eller dokumentet; forklar ingen markering. | Tekstvalg giver relevante tekstkontroller; flere markerede objekter angives tydeligt. Irrelevante kontroller fremstår ikke brugbare. |
| ED-04 Præcision | Målfast arbejde har navngivne talfelter og synlige enheder; træk er ikke eneste vej. | En opgave med bredde/højde i mm kan løses præcist. Proportionslås har tydelig tilstand, hvis den findes. |
| ED-05 Fortrydelse | Historik følger forståelige brugerhandlinger. | Én afsluttet flytning/rotation/skalering fortrydes normalt i ét trin. Afbrydelse gendanner udgangspunktet; talfelter og sammensatte handlinger indgår. |
| ED-06 Find tilbage | Der er en forståelig vej til projekt/markering og hjælp. | Et emne uden for udsnittet kan genfindes. Hjælp, som lukkes, kan åbnes igen. Smal visning har tydelig retur fra støttepanel. |
| ED-07 Tom og optaget | Skeln tomt projekt, ingen markering, indlæsning, tom søgning og forbindelsesfejl. | Første projekt har konkret startmulighed. En optaget handling viser sandfærdig status, ikke opdigtet procent eller færdigmelding. |
| ED-08 Eksport/aflevering | Vis relevante emner, størrelser/enheder og format før en konsekvensfuld aflevering. | Brugeren kan rette valgene uden at starte forfra. Eksport, kømodtagelse og fysisk udførelse har forskellige statusord. |
| ED-09 Effektivitet | Begyndere får hjælp; rutinerede kan arbejde hurtigt. | Hyppige handlinger er lette at finde; ekstra valg er tilgængelige uden at dominere. Genveje kan opdages, men er ikke eneste betjening. |
| ED-10 Visuel kvalitet | Konsistent spacing, typografi, kant/radius, ikonstil og accentbrug. | Primær handling, valgt tilstand, fokus og deaktivering kan skelnes. Dekoration og store tomme topområder fortrænger ikke arbejdsområdet. |

Excalidraw inspirerer ED-02/03/07, Penpot ED-01/04/08, tldraw ED-05,
GDevelop ED-04/06/09 og Fluent 2/Material 3 ED-01/09/10.
Det er vores overførsel af mønstre, ikke en påstand om, at produkterne er
valideret til alle danske skolebørn. [Præcise kilder](UI-UX-KILDER-20261111.md).

## Tilgængelighed som grundlag

- **Kontrast:** WCAG 1.4.3 AA kræver 4,5:1 for normal tekst og 3:1 for stor
  tekst (mindst 18 pt eller 14 pt fed efter standardens definition).
  Nødvendig visuel information om kontroller/tilstande følger 1.4.11 AA,
  normalt 3:1 mod tilstødende farver med kriteriets undtagelser.
  Farve formidler ikke betydning alene (1.4.1 A).
- **Klikmål:** 2.5.8 AA kræver 24 × 24 CSS-pixel eller en specificeret
  undtagelse, bl.a. den definerede afstandsregel. Vores projektmål er
  mindst 44 × 44 for selvstændige handlingsknapper, især på fælles touchskærme.
  Det er ikke AA-minimum; 44 × 44 er størrelsen i 2.5.5 AAA.
  Tætte editorværktøjer kræver en begrundet, brugbar løsning og afprøvning.
- **Tastatur/fokus:** Funktionalitet kan betjenes med tastatur efter 2.1.1
  med kriteriets undtagelse; tastaturfælder undgås efter 2.1.2.
  Meningsfuld fokusorden, synligt fokus og fokus ikke helt
  skjult af applikationen følger 2.4.3/2.4.7/2.4.11. Dialoger får passende
  fokus og tilbagefører det. Brug native HTML, når det passer.
- **Input:** Træk-og-slip har et alternativ uden træk efter 2.5.7 AA med
  undtagelserne. Nødvendig hjælp afhænger ikke af hover. Meningsfulde navne,
  roller og tilstande følger 4.1.2.
- **Fejl/status:** Fejl identificeres i tekst med labels og kendte
  rettelsesforslag (3.3.1–3.3.3). Statusbeskeder kan opfattes af hjælpemidler
  uden nødvendigt fokusskift (4.1.3).
- **Login:** Understøt indsætning og normal adgangskodehåndtering; følg
  3.3.8 AA med undtagelserne. Tilgængelighed kræver ikke fjernelse af lærerlogin.
- **Zoom/bevægelse:** Vurder tekstforstørrelse og reflow efter 1.4.4/1.4.10
  med undtagelserne. Rumligt canvas kan have andre krav end omliggende
  formularer. Respektér reduceret bevægelse; animér ikke essentiel information væk.

## Konkrete projektforløb

**Elev afleverer til 3D-print:** Åbn aftalt elevindgang, vælg og se filen,
træf nødvendige valg, send til kø og se korrekt kvittering. Bevar fil/valg
ved rettelige fejl. Tilføj ikke login til en aftalt offentlig funktion.
En prøve må ikke starte et fysisk print uden konkret mandat.

**Voksen rydder printplade:** Find Læreradgang på printfladen. Normal
autorisation fører direkte til navngivne printere og den relevante
“Pladen er ryddet”-handling. Forklar utilgængelig printer. En agent kvitterer
ikke en fysisk ryddet plade for at teste UI.

**Barn arbejder kreativt:** Genkendelig start/åbn, forståelige kerneværktøjer,
fortrydelse og klar forskel på gem, eksport og aflevering. Avancerede valg
er tilgængelige uden at dominere første møde. Denne vejledning bestiller
ikke ny elevlogin eller ny datalagring.

## Proportional kontrol

UI/UX-specialisten bruges altid, men undersøger kun det berørte. En label
vurderes kort i kontekst; et ændret flow kræver normalvej, relevante fejl,
tastatur og relevante målgrupper. Ved større visuelle ændringer gemmes et
brugbart før/efter-billede med viewport, når baseline findes. Ingen fuld
billedserie for hver lille ændring.

Nye hovedflows, store ombygninger og væsentlig usikkerhed kræver en lille
realistisk brugerprøve med berørte grupper. Adgang til deltagere aftales med
brugeren. Hvis prøven ikke kan gennemføres i opgaven, registreres den som
udestående, og konklusionen begrænses til ekspert-/funktionsreview; en godkendt
plan er ikke dokumenteret brugervenlighed. Simulerede elevreaktioner er ikke
brugerresearch; indfør ikke produkttracking som genvej.

Automater finder nogle semantik-, kontrast- og tilstandsfejl. De beviser ikke
forståelighed, værdighed, fysisk maskinestatus eller fuld tilgængelighed.
Adskil derfor observation, ekspertvurdering og hypotese i reviewet.
