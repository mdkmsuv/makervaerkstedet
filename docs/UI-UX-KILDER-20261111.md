# UI/UX-research — konkrete mønstre til Makerværkstedet

Undersøgt 11. november 2026. Formål: give udviklingsagenten og UI/UX-specialisten
et konkret fagligt grundlag for bedre editorer, elevforløb og lærerbetjening.
Målgrupper: 2.–10. klasse i dansk folkeskole, IT-vante voksne og voksne med
begrænset IT-erfaring. Ingen biblioteker er installeret, og ingen produktflader
er ombygget som del af researchen.

## Konklusion og prioritering

Brug **Excalidraw og Penpot** til arbejdsflade, markering og egenskaber,
**tldraw** til fortrydelse og **GDevelop** til præcise kontroller og genfindelig
hjælp. **Fluent 2 og Material 3** giver konkrete principper for værktøjslinjer,
støttepaneler, tilstande og fremdrift. De er hovedreferencer for UI-håndværket.

Brug **GOV.UK, USWDS og W3C COGA** til forståelige opgaver, sprog og fejl.
**Blockly, Scratch og UNICEF RITEC** supplerer læring og brugerens mulighed for
at prøve, forstå og rette. **WCAG/APG** er tilgængelighedsgrundlaget; de er ikke
en opskrift på et godt kreativt værksted.

Kravene er omsat til UX-01–12 og ED-01–10 i [UI/UX-krav](UI-UX-KRAV.md).
Dette dokument er et opslagsværk: læs den relevante reference ved en konkret
beslutning, ikke samtlige repos før en lille rettelse.

## Metode og begrænsninger

- Tolv GitHub-kandidater blev undersøgt gennem officielle repos og tilknyttede
  docs eller konkrete kildefiler. Hertil kommer førstehåndsvejledninger fra
  Microsoft, Google, Adobe, W3C, GOV.UK, UNICEF og Nielsen Norman Group.
- Udvælgelsen bygger på konkrete mønstre og relevans, ikke stjernetal eller
  en rangliste over “bedste UX”. Aktivitetsoplysninger er øjebliksbilleder.
- Vi skelner referenceprodukt, komponentbibliotek, normativ standard,
  informativ vejledning og vores egne projektvalg.
- Vi kopierer ikke et helt visuelt udtryk. React-, Android- eller andre
  platformsvalg følger ikke med, blot fordi et mønster er nyttigt.
- Før eventuelt kode-/assetgenbrug skal den aktuelle licens og kompatibilitet
  kontrolleres. Licenser er ikke gennemgået for alle kandidater. tldraws
  begrænsning er særskilt verificeret nedenfor.
- Ingen af disse produkter beviser i sig selv egnethed for hele 2.–10. klasse.
  Klasseprofilerne i kravene er praktiske projektvalg, ikke universelle
  udviklingstrin. Effekten skal efterprøves med relevante brugere.

## Moderne editorer: hvad vi konkret kan lære

### 1. Excalidraw — markering og relevante kontroller

[Officielt repo: excalidraw/excalidraw](https://github.com/excalidraw/excalidraw).
Referenceprodukt og React-baseret indlejringsbibliotek.

Den undersøgte [Actions-komponent](https://github.com/excalidraw/excalidraw/blob/master/packages/excalidraw/components/Actions.tsx)
vælger egenskaber efter aktivt værktøj og markerede elementer og har kompakte
og mobile varianter. [WelcomeScreen](https://github.com/excalidraw/excalidraw/blob/master/packages/excalidraw/components/welcome-screen/WelcomeScreen.tsx)
viser hjælp ved konkrete dele af arbejdsfladen.

**Overførsel:** Adskil værktøjsvalg og egenskabsændring. Valg af et andet objekt
skal opdatere panelets værdier og tydeligt vise, hvad der ændres. Et tomt projekt
skal have en konkret startmulighed. På smalle skærme kan ekstra egenskaber samles,
men nødvendige handlinger skal fortsat findes.

**Begrænsning:** Den håndtegnede whiteboardstil er ikke et mål for målfast
laser-/3D-arbejde. [Releases](https://github.com/excalidraw/excalidraw/releases)
viser produkt- og sikkerhedsrettelser; dette er ikke et løfte om en bestemt
implementations tilgængelighed eller egnethed til skolen.

### 2. Penpot — stabil arbejdsflade og kontrolleret eksport

[Officielt repo: penpot/penpot](https://github.com/penpot/penpot), udviklet af
Kaleidos. Professionelt referenceprodukt.

[Interfaceturen](https://help.penpot.app/user-guide/first-steps/the-interface/)
beskriver værktøjskasse, lag, kontekstuelle egenskaber, historik og gemmestatus.
[Eksportvejledningen](https://help.penpot.app/user-guide/export-import/exporting-layers/)
viser kontrol af navne, størrelser og formater samt fremdrift og fejl.

**Overførsel:** Hold kerneværktøjer stabile. Vis mål og enheder, når en fysisk
størrelse er relevant. Lad brugeren finde overlappende eller skjulte objekter.
Adskil gemmer/gemt/fejl og eksport/aflevering. Bevar projekt og valg ved fejl.

**Begrænsning:** Et professionelt designværktøjs fulde panelmængde passer ikke
automatisk til begyndere. Repoets [releasehistorik](https://github.com/penpot/penpot/releases)
blev kontrolleret; version 2.19.1 var dateret 11. november 2026.

### 3. tldraw — forståelig historik

[Officielt repo: tldraw/tldraw](https://github.com/tldraw/tldraw).
Canvas-SDK og editorreference.

[Historikvejledningen](https://tldraw.dev/sdk-features/history) beskriver
gruppering af ændringer og tilbagevenden til en igangværende handlings start.
**Overførsel:** Én flytning, skalering eller rotation fortrydes normalt i ét
trin. Afbrydelse gendanner udgangspunktet; talfelter og sammensatte handlinger
skal også afprøves. Vis fortryd/genopret, selv om genveje også findes.

[v5.4.0-noterne](https://tldraw.dev/releases/v5.4.0) dokumenterer rettelser i
historik og markering. **Licensbegrænsning:** Den kontrollerede
[LICENSE.md](https://github.com/tldraw/tldraw/blob/main/LICENSE.md) begrænser
produktionsbrug uden særskilt licens. Dette er en mønsterreference, ikke en
anbefaling om at installere et frit OSS-bibliotek i produktet.

### 4. GDevelop — præcision og en vej tilbage

[Officielt repo: 4ian/GDevelop](https://github.com/4ian/GDevelop).
Omfattende spilværktøj og editorreference.

[Scene-editoren](https://wiki.gdevelop.io/gdevelop5/interface/scene-editor/)
har blandt andet positions-/størrelsesfelter, proportionslås og fokus på valgte
objekter. [Preferences](https://wiki.gdevelop.io/gdevelop5/preferences/)
beskriver genåbning af hints, layoutnulstilling og genveje.

**Overførsel:** Præcis ændring må ikke kræve små trækhåndtag. Proportionslås
skal have tydelig tilstand; mistede objekter og lukket hjælp skal kunne
genfindes. Avancerede muligheder må ikke være en forudsætning for første opgave.

**Begrænsning:** Spilværktøjets fulde arbejdsgang skal ikke kopieres.
[Releasehistorikken](https://github.com/4ian/GDevelop/releases) viste 5.6.285
fra 21. oktober 2026; det er aktivitet, ikke aldersvalidering.

## Nutidige designsystemer: præcise mønstre

### Fluent 2 — værktøjer og fremdrift

Microsofts [Toolbar](https://fluent2.microsoft.design/components/web/react/core/toolbar/usage)
grupperer opgavens handlinger, adskiller destruktive valg og bruger tekst i
overflow-menuer. **Projektvalg:** Global navigation, dokumenthandlinger og
markeringsværktøjer skal kunne skelnes. Primære begynderhandlinger afhænger
ikke af hover eller kendte genveje.

[Progress bar](https://fluent2.microsoft.design/components/web/react/core/progressbar/usage)
skelner målt og ubestemt fremdrift. **Projektvalg:** Vis procent, når data
understøtter den; skeln overførsel, kø og maskinstatus. “100 % overført” er
ikke fysisk færdigmelding. Der skal være en forståelig vej videre ved fejl.
Det tilknyttede bibliotek er [microsoft/fluentui](https://github.com/microsoft/fluentui);
reglerne kræver ikke, at det tilføjes til projektet.

### Material 3 — canvas og støttepanel

[Canonical layouts](https://m3.material.io/foundations/layout/canonical-examples/overview)
og Googles [supporting pane-guide](https://developer.android.com/develop/adaptive-apps/guides/build-a-supporting-pane-layout)
skelner hovedindhold og støtteinformation.

**Projektvalg:** Canvas er hovedområdet; egenskaber og hjælp har en klar
sekundær placering. Panelåbning/resize må ikke miste projekt eller markering.
Smal visning skal have en tydelig vej tilbage. En bestemt kolonnebrøk er ikke
et universelt kvalitetsmål. Implementeringsguiden er Android-specifik;
kopiér ikke dens komponenter eller dp-breakpoints ukritisk til web.

### Spectrum — komponenttilstande

Adobes [States](https://spectrum.adobe.com/foundations/behavior/states)
beskriver blandt andet normal, hover, trykket, fokus, disabled og valgt.
**Projektvalg:** Aktivt værktøj, markering, fokus og utilgængelig handling skal
kunne skelnes; stærk accent på alle paneler giver ikke en tydelig rangorden.
[React Aria Quality](https://react-aria.adobe.com/quality) supplerer med
interaktionskvalitet; meningsfulde labels og passende brug er stadig produktets
ansvar, selv med et komponentbibliotek.

## Øvrige GitHub-kandidater

| Officiel kilde | Type og anvendelse | Begrænsning |
|---|---|---|
| [alphagov/govuk-frontend](https://github.com/alphagov/govuk-frontend) | Komponenter og UX-mønstre. [Spørgsmålssider](https://design-system.service.gov.uk/patterns/question-pages/) og [fejloversigt](https://design-system.service.gov.uk/components/error-summary/) hjælper med konkrete valg og rettelser. | Formularer er ikke hele modellen for kreativt arbejde; undgå unødige trin. |
| [w3c/aria-practices](https://github.com/w3c/aria-practices) | Semantik, fokus og forventet tastaturadfærd. [Read Me First](https://www.w3.org/WAI/ARIA/apg/practices/read-me-first/) advarer mod at forveksle rolle med implementeret funktion. | Referenceeksempler skal afprøves i relevante browsere/hjælpemidler. |
| [uswds/uswds](https://github.com/uswds/uswds) | Designsystem med [principper](https://designsystem.digital.gov/design-principles/) om reelle brugerbehov og observation frem for antagelser. | Amerikansk myndighedskontekst; overtag ikke identitet eller hele biblioteket. |
| [adobe/react-spectrum](https://github.com/adobe/react-spectrum) | React-komponenter og interaktionsreference til labels, fokus, input og tilstande. | Frameworkbinding; ingen automatisk garanti for det samlede produkt. |
| [carbon-design-system/carbon](https://github.com/carbon-design-system/carbon) | IBM-designsystem; [Empty states](https://www.carbondesignsystem.com/building-blocks/core/patterns/empty-states) skelner første brug, tomt resultat og problemer. | Enterprise-kompleksitet skal ikke overføres til begyndere. |
| [RaspberryPiFoundation/blockly](https://github.com/RaspberryPiFoundation/blockly) | Visuel programmering; [tastaturnavigation](https://docs.blockly.com/guides/configure/keyboard-nav/) giver alternativer til præcis trækbetjening. | Bloktræers navigation er et specialtilfælde, ikke en standard til alle formularer. |
| [scratchfoundation/scratch-editor](https://github.com/scratchfoundation/scratch-editor) | Kreativt referenceprodukt. [Menukoden](https://github.com/scratchfoundation/scratch-editor/blob/develop/packages/scratch-gui/src/components/menu-bar/menu-bar.jsx) viser tutorial- og gendannelseshandlinger. | Det gamle [scratch-gui](https://github.com/scratchfoundation/scratch-gui) er arkiveret; den farverige stil/panelmængde er ikke bevis på børnevenlighed. |
| [Heydon/Inclusive-Components](https://github.com/Heydon/Inclusive-Components) | Forfattervejledning; [Cards](https://inclusive-components.design/cards/) belyser klikflade, semantik, tekstmarkering og fokus. | Ældre kodeeksempler; aktuel vedligeholdelsesstatus er ikke fastslået. |

De fire moderne editorer og disse otte kandidater udgør de tolv undersøgte
GitHub-kandidater. Fluent UI er et supplerende bibliotekslink til den læste
designvejledning, ikke endnu et gennemgået implementationsvalg.

## Forståelighed og forskellige brugere

[W3C COGA](https://www.w3.org/TR/coga-usable/) anbefaler klare formål,
genkendelige mønstre, almindelige ord og hjælp tæt på opgaven. Det er
supplerende vejledning, ikke et ekstra WCAG-krav. Vi anvender det til
forståelige tekster og værdig hjælp, ikke til at diagnosticere brugere.

[GOV.UK om digital støtte](https://www.gov.uk/service-manual/helping-people-to-use-your-service/how-your-assisted-digital-support-will-be-assessed)
understreger research i kunnen, tillid og støttebehov.
[Nielsens heuristikker](https://www.nngroup.com/articles/ten-usability-heuristics/)
supplerer med synlig status, genkendelse, brugerens kontrol og effektive veje.
Vores accept er konkret opgavegennemførelse, ikke en abstrakt “intuitiv”-score.

[UNICEF/LEGO RITEC](https://www.unicef.org/childrightsandbusiness/workstreams/responsible-technology/online-gaming/ritec-design-toolbox)
handler om trivsel i digital leg, herunder handlekraft, mestring og kreativitet.
Forskningsgrundlaget fokuserer på 8–12-årige og dokumenterer ikke alle forhold
for 2.–10. klasse eller fysisk maskinbetjening. Vi låner fokus på forståelige
valg, mulighed for at rette fejl og passende hjælp; ikke et krav om point,
animation eller spilmekanik.

GOV.UK giver konkrete mønstre for [kontrol af oplysninger](https://design-system.service.gov.uk/patterns/check-answers/)
og [fejlbeskeder](https://design-system.service.gov.uk/components/error-message/).
Vi viser relevante valg før konsekvensfulde handlinger og bevarer gyldigt input.
Der indføres ikke bekræftelsesdialoger til hver lille handling.

## WCAG som grundlag, ikke designopskrift

[WCAG 2.2](https://www.w3.org/TR/WCAG22/) er fortsat W3C Recommendation.
[WCAG 3](https://www.w3.org/WAI/standards-guidelines/wcag/wcag3-intro/) er en
ufuldstændig Working Draft og erstatter ikke WCAG 2.2 som gældende reference.
At en standard ikke er ny, gør ikke dens tastatur- eller kontrastkrav irrelevante.
Den kan imidlertid ikke alene afgøre, om vores editor er god at bruge.

Præcise forklaringer: [tekstkontrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html),
[kontrolkontrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html),
[klikmål](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html),
[træk-alternativ](https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html),
[synligt fokus](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html),
[fokus ikke skjult](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html),
[statusbeskeder](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html)
og [tilgængeligt login](https://www.w3.org/WAI/WCAG22/Understanding/accessible-authentication-minimum.html).
Understanding-siderne er informative forklaringer; selve kriterierne står i standarden.

Vores 44 × 44 CSS-pixel-mål for selvstændige knapper er et projektvalg.
WCAG 2.5.8 AA angiver 24 × 24 med specificerede undtagelser; de to størrelser
må ikke omtales som samme krav.

## Hvordan viden bliver til accept

Fem supplerende værktøjs- og skill-repos er vurderet særskilt i
[UI/UX-værktøjer](UI-UX-VAERKTOEJER.md): Playwright MCP, axe-core,
Anthropic frontend-design, Vercel web-design-guidelines og UI UX Pro Max.
Listen beskriver konkret anvendelse, eksisterende overlap og licensforhold.

UI/UX-agenten vælger kun de relevante kriterier og vurderer den renderede
oplevelse. For en editor: vælg et objekt, ændr det præcist, fortryd, genfind det
og kontrollér relevant eksport. For pladerydning: find lærerindgang, log ind,
se navngiven printer og tydelig handling uden at aktivere en fysisk rydning.
For elevaflevering: vælg fil, ret fejl og forstå køkvitteringen uden at starte
en maskine.

Ved større ændringer suppleres ekspertreview med realistiske brugeropgaver
for de berørte klasse-/erfaringsgrupper. Hvis adgang til deltagere mangler,
står brugerprøven som udestående; et ekspertreview kaldes ikke brugerbevis.
Dokumentér faktisk observerede
problemer og nødvendig hjælp. Screenshots og automater beviser ikke børns
forståelse, og ingen af kilderne begrunder ny elevtracking eller datalagring.
