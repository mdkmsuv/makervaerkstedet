// Frivillige, selvbyggende guider til laserprojekterne.
// Guiderne beskriver handlinger og valg; de indsætter ikke geometri og evaluerer ikke elevens svar.

export const LASER_GUIDES = [
  {
    id: 'nameplate', title: 'Navneskilt', level: 0,
    intro: 'Lav et lille skilt, der viser dit navn og et symbol, du selv vælger.',
    skill: 'Tekst, størrelse og forskellen på en kant og en overflade.',
    reflection: 'Hvilket valg gør skiltet til dit, og hvad skal laseren gøre ved det?',
    steps: [
      {title: 'Vælg en skiltform', text: 'Tilføj en firkant eller en cirkel, og beslut hvilken form der passer til dit skilt.', hint: 'Et forslag er cirka 80–120 mm bredt.', target: '[data-add="rect"]', check: 'Kan du forklare, hvorfor du valgte formen?'},
      {title: 'Skriv dit navn', text: 'Tilføj tekst, og skriv det navn eller ord, som skiltet skal vise.', target: '#add-text', check: 'Kan en anden læse ordet uden at spørge dig?'},
      {title: 'Tilføj et symbol', text: 'Tilføj én enkel form som symbol, og placér den ved siden af eller under navnet.', target: '[data-add="circle"]', check: 'Hvad fortæller symbolet om dig?'},
      {title: 'Giv teksten plads', text: 'Vælg teksten og tilpas størrelsen, så den står inden for kanten.', hint: 'Lad lidt luft være mellem bogstaverne og kanten.', target: '#object-width', check: 'Er der luft hele vejen rundt om teksten?'},
      {title: 'Vælg en laserhandling', text: 'Vælg om kanten skal skæres, og om navnet skal graveres på overfladen.', target: '[data-op="engrave"]', check: 'Kan du pege på, hvad der bliver skåret og hvad der bliver graveret?'},
      {title: 'Se resultatet', text: 'Åbn forhåndsvisningen og kig efter tekst, kant og eventuelle dele uden for arbejdsområdet.', target: '#preview-button', check: 'Hvad vil du ændre, hvis noget ikke ser rigtigt ud?'},
      {title: 'Tænk tilbage', text: 'Beslut om din løsning er klar til læreren, eller vælg én ændring mere.', check: 'Kan du beskrive én bevidst ændring, du lavede?'}
    ]
  },
  {
    id: 'key-tag', title: 'Taske- eller nøglemærke', level: 0,
    intro: 'Design et mærke med navn, form og et separat hul til ophængning.',
    skill: 'Placering med hensyn til kant og hul.',
    reflection: 'Hvordan sørgede du for, at mærket både kan læses og hænge fast?',
    steps: [
      {title: 'Vælg yderformen', text: 'Tegn en enkel yderform til mærket, og vælg om den skal være rund eller kantet.', hint: 'Prøv cirka 60–90 mm i den længste retning.', target: '[data-add="rect"]', check: 'Har formen et tydeligt sted til et ophæng?'},
      {title: 'Lav ophængshullet', text: 'Tilføj en cirkel og placér den tæt på kanten med god afstand til kanten.', hint: 'Et lille hul på cirka 6–10 mm kan være et startforslag.', target: '[data-add="circle"]', check: 'Kan en ring eller snor gå igennem uden at ramme kanten?'},
      {title: 'Skriv mærkets ord', text: 'Tilføj dit navn eller et kort ord, og placér det i den plads der er tilbage.', target: '#add-text', check: 'Er ordet placeret uden at dække hullet?'},
      {title: 'Tilpas uden at strække', text: 'Ændr størrelsen på de valgte dele. Brug “Behold figurens form”, når du ændrer mål.', hint: 'Et rundt hul skal blive rundt.', target: '#object-width', check: 'Er hullet stadig rundt efter ændringen?'},
      {title: 'Vælg skæring', text: 'Sæt yderform og hul til Skær, og vælg en passende handling for teksten.', target: '[data-op="cut"]', check: 'Hvilke linjer skal gå hele vejen gennem materialet?'},
      {title: 'Forhåndsvis og vurder', text: 'Se hele mærket i forhåndsvisningen, og beslut om tekst eller afstand skal justeres.', target: '#preview-button', check: 'Hvad er den vigtigste sikkerhed for, at hullet virker?'}
    ]
  },
  {
    id: 'room-sign', title: 'Dør- eller rumskilt', level: 0,
    intro: 'Lav et skilt, som hjælper andre med at finde et rum.',
    skill: 'Læsbar tekst, symbol og placering inden for en kant.',
    reflection: 'Hvad vil en person, der ikke kender rummet, forstå af dit skilt?',
    steps: [
      {title: 'Vælg et sted', text: 'Beslut hvilket rum eller hvilken funktion skiltet skal vise, før du tegner.', check: 'Kan du sige, hvem der skal bruge skiltet?'},
      {title: 'Tegn skiltets kant', text: 'Tilføj en firkant eller cirkel og giv den en størrelse, der passer til stedet.', hint: 'Et forslag er 100–160 mm bredt.', target: '[data-add="rect"]', check: 'Er der plads til at læse skiltet på afstand?'},
      {title: 'Skriv rummets navn', text: 'Tilføj rummets navn som tekst, og vælg en størrelse der passer til kanten.', target: '#add-text', check: 'Kan ordet læses hurtigt?'},
      {title: 'Tilføj et hjælpesymbol', text: 'Tilføj én enkel form som symbol, og vælg placeringen selv.', hint: 'Et symbol må gerne være mindre end teksten.', target: '[data-add="circle"]', check: 'Fortæller symbolet noget ekstra?'},
      {title: 'Vælg skær eller gravér', text: 'Beslut hvilke dele der skal skæres, og hvilke der skal graveres.', target: '[data-op="engrave"]', check: 'Kan du forklare forskellen på de to processer?'},
      {title: 'Test skiltet', text: 'Åbn forhåndsvisningen og forestil dig, at du ser skiltet fra døren.', target: '#preview-button', check: 'Hvad ville du forbedre for en bruger?'},
      {title: 'Skriv din begrundelse', text: 'Vælg én ændring, du vil beholde, fordi den gør skiltet tydeligere.', check: 'Kan du begrunde ændringen med en bruger eller et sted?'}
    ]
  },
  {
    id: 'medal', title: 'Medalje med navn', level: 0,
    intro: 'Design en medalje til en person, en præstation eller en anledning.',
    skill: 'Tekst og symbol inden for en rund kant med plads til et separat båndhul.',
    reflection: 'Hvordan viser form, tekst og symbol medaljens anledning?',
    steps: [
      {title: 'Vælg anledningen', text: 'Beslut hvem eller hvad medaljen skal fejre, og vælg et kort ord.', check: 'Er ordet kort nok til at være tydeligt?'},
      {title: 'Tegn medaljens krop', text: 'Tilføj en cirkel og vælg en størrelse, der giver plads til tekst og symbol.', hint: 'Prøv cirka 50–70 mm i diameter.', target: '[data-add="circle"]', check: 'Har du gemt en fri kant rundt om indholdet?'},
      {title: 'Lav et båndhul', text: 'Tilføj en separat cirkel tæt på toppen, men med materiale mellem hullet og kanten.', hint: 'Et hul på cirka 6–8 mm er kun et startforslag.', target: '[data-add="circle"]', check: 'Er der stadig en stærk kant rundt om hullet?'},
      {title: 'Tilføj navn og symbol', text: 'Skriv ordet og tilføj ét symbol. Placér dem, så de ikke rammer båndhullet.', target: '#add-text', check: 'Kan teksten og symbolet ses uden at konkurrere?'},
      {title: 'Vælg processer', text: 'Vælg Skær til medaljens kant og hul, og vælg Gravér til tekst eller symbol.', target: '[data-op="cut"]', check: 'Hvilke dele skal være et hul gennem materialet?'},
      {title: 'Se medaljen ovenfra', text: 'Åbn forhåndsvisningen og tjek kant, hul og læsbarhed.', target: '#preview-button', check: 'Er der en del, du vil flytte eller gøre mindre?'}
    ]
  },
  {
    id: 'pattern-bookmark', title: 'Mønsterbogmærke', level: 1,
    intro: 'Byg et mønster på et bogmærke ved at gentage og ændre en enkel figur.',
    skill: 'Kopiering, afstand, rotation og orden i en komposition.',
    reflection: 'Hvilke valg gør dit mønster regelmæssigt eller uroligt?',
    steps: [
      {title: 'Tegn bogmærket', text: 'Tilføj en firkant som bogmærkets kant, og vælg retning på siden.', hint: 'Et vandret forslag er cirka 130 × 30 mm. Slå “Behold figurens form” fra, så du kan sætte bredde og højde hver for sig.', target: '[data-add="rect"]', check: 'Passer målet til arbejdsområdet og din bog?'},
      {title: 'Sæt målene', text: 'Sæt bredden til cirka 130 mm og højden til cirka 30 mm, eller vælg dine egne mål. Slå “Behold figurens form” fra først.', target: '#object-width, #object-height, #lock-ratio', check: 'Har du valgt både bredde og højde med vilje?'},
      {title: 'Lav én figur', text: 'Tilføj en lille cirkel, stjerne eller anden enkel figur, og vælg om den skal skæres eller graveres.', target: '[data-add="star"]', check: 'Er figuren enkel nok til at gentage?'},
      {title: 'Placér den første', text: 'Vælg og flyt figuren, så den står med luft til bogmærkets kant.', target: '[data-tool="select"]', check: 'Har du selv valgt startpunktet?'},
      {title: 'Gentag med kopi', text: 'Lav kopier af figuren og flyt dem, så der opstår et mønster.', target: '#duplicate', check: 'Er afstanden mellem nogle af figurerne bevidst?'},
      {title: 'Drej eller variér', text: 'Drej mindst én kopi eller lav en lille variation, som du selv kan forklare.', target: '#rotate-right', check: 'Hvilken figur er anderledes, og hvorfor?'},
      {title: 'Forhåndsvis og vælg', text: 'Åbn forhåndsvisningen, se efter at ingen figur går ud over kanten, og beslut om mønsteret er klar.', target: '#preview-button', check: 'Hvad fortæller mønsteret om din idé?'}
    ]
  },
  {
    id: 'club-logo', title: 'Hold- eller klublogo', level: 1,
    intro: 'Sammensæt former og tekst til et logo, som en bestemt gruppe kan genkende.',
    skill: 'Del og helhed, centrering og gruppering.',
    reflection: 'Hvilke dele skal ses som én helhed, og hvad hjælper brugeren med at genkende den?',
    steps: [
      {title: 'Vælg modtageren', text: 'Beslut hvilket hold, hvilken klub eller hvilken gruppe logoet skal repræsentere.', check: 'Hvilken idé skal andre forbinde med logoet?'},
      {title: 'Vælg tre former', text: 'Tilføj op til tre enkle former, og giv hver form en rolle i logoet.', target: '[data-add="circle"]', check: 'Har hver form en grund til at være med?'},
      {title: 'Skriv gruppens navn', text: 'Tilføj en kort tekst og vælg, om den skal stå over, under eller ved siden af formerne.', target: '#add-text', check: 'Kan navnet læses uden at kende gruppen?'},
      {title: 'Byg kompositionen', text: 'Flyt delene, så logoet enten bliver kompakt eller bredt. Vælg én af de to løsninger.', target: '#object-width', check: 'Hvilken komposition passer bedst til din anvendelse?'},
      {title: 'Saml kun efter behov', text: 'Vælg flere dele og gruppér dem, hvis de skal flyttes sammen. Gruppering holder delene redigerbare.', target: '#group-objects', check: 'Hvilke dele skal stadig kunne redigeres hver for sig?'},
      {title: 'Vælg laserhandlinger', text: 'Beslut hvad der skal skæres, og hvad der skal graveres eller markeres.', target: '[data-op="engrave"]', check: 'Kan du forklare lagdelingen i logoet?'},
      {title: 'Sammenlign og forbedr', text: 'Åbn forhåndsvisningen, sammenlign din løsning med formålet, og lav én forbedring.', target: '#preview-button', check: 'Hvilken ændring gør logoet lettere at genkende?'}
    ]
  },
  {
    id: 'own-silhouette', title: 'Egen silhuet', level: 2,
    intro: 'Skab en egen lukket silhuet ved at tegne, ændre og kombinere former.',
    skill: 'Punkter, konturer og geometriske operationer.',
    reflection: 'Hvilken geometrisk ændring gav størst forskel på motivet?',
    steps: [
      {title: 'Vælg motivet', text: 'Beslut om silhuetten skal være et dyr, et køretøj, en plante eller et abstrakt tegn.', check: 'Hvilke ydre kendetegn skal kunne ses?'},
      {title: 'Tegn grundformen', text: 'Lav en enkel lukket kontur med punktværktøjet. Start med få punkter.', target: '[data-tool="polyline"]', check: 'Er konturen lukket, så den kan blive en sammenhængende form?'},
      {title: 'Ret et punkt', text: 'Vælg et punkt på konturen og flyt det, så motivets kendetegn bliver tydeligere.', hint: 'Små ændringer er lettere at vurdere end mange på én gang.', target: '[data-tool="nodes"]', check: 'Hvilket kendetegn ændrede punktet?'},
      {title: 'Tilføj eller fjern en del', text: 'Tilføj en enkel form, vælg delene og brug “Saml til én figur” eller “Lav hul med øverste figur” efter din idé.', target: '#subtract', check: 'Hvad blev tilføjet eller fjernet i silhuetten?'},
      {title: 'Vælg skæring', text: 'Sæt silhuetten til Skær, og sørg for at konturen er lukket og uden uønskede løse dele.', target: '[data-op="cut"]', check: 'Hvilken samlet form skal materialet ende med?'},
      {title: 'Forhåndsvis konturen', text: 'Åbn forhåndsvisningen og se, om silhuetten stadig kan genkendes som dit motiv.', target: '#preview-button', check: 'Hvilken del vil du forenkle eller gøre tydeligere?'},
      {title: 'Vælg forbedringen', text: 'Lav én sidste ændring, som du kan begrunde ud fra motivets form eller brug.', check: 'Kan du forklare, hvorfor ændringen hjælper?'}
    ]
  },
  {
    id: 'stencil', title: 'Genbrugelig stencil', level: 2,
    intro: 'Lav et motiv, hvor de indvendige øer bliver siddende ved hjælp af rigtige broer.',
    skill: 'Positiv og negativ form samt sammenhæng i et udskåret materiale.',
    reflection: 'Hvorfor er hver bro nødvendig, og hvordan påvirker den motivet?',
    steps: [
      {title: 'Vælg stencilmotivet', text: 'Beslut hvilket enkelt tegn eller motiv der skal kunne males gennem stencilen.', check: 'Hvilke indvendige områder skal blive til øer?'},
      {title: 'Tegn yderrammen', text: 'Tilføj en yderramme, der er stor nok til at holde motivet og broer.', hint: 'Lad der være cirka 8–12 mm til rammens kant som arbejdsforslag.', target: '[data-add="rect"]', check: 'Er der plads til at holde stencilen?'},
      {title: 'Lav et hulmotiv', text: 'Lav et ringmotiv med en ydre cirkel og en mindre indre cirkel. Vælg de to cirkler og tryk “Lav hul med øverste figur”.', target: '#subtract', check: 'Er midten af ringen blevet en åbning?'},
      {title: 'Byg en bro', text: 'Tilføj et smalt rektangel hen over åbningen, så den forbinder den indre ø med resten af materialet.', target: '[data-add="rect"]', check: 'Holder broen øen fast uden at skjule motivet?'},
      {title: 'Lav hul med broen', text: 'Vælg kun åbningen og broen, med broen øverst, og tryk “Lav hul med øverste figur”. Lad yderrammen være separat.', hint: 'Så bliver broen en del af den form, der bliver tilbage.', target: '#subtract', check: 'Er yderrammen stadig en selvstændig del?'},
      {title: 'Vælg skæring', text: 'Sæt yderramme og den færdige åbning til Skær, og fjern dele der ikke skal med.', target: '[data-op="cut"]', check: 'Er stencilen én sammenhængende form med åbninger?'},
      {title: 'Forhåndsvis og eftertænk', text: 'Åbn forhåndsvisningen og vurder, om broer og åbninger giver det ønskede motiv.', target: '#preview-button', check: 'Hvilken bro ville du flytte, hvis motivet blev uklart?'}
    ]
  },
  {
    id: 'table-sign', title: 'Bordskilt med fod', level: 3,
    intro: 'Design et bordskilt og en fod, der kan prøves i det materiale, I bruger.',
    skill: 'Mål, materialetykkelse, pasform og fysisk afprøvning.',
    reflection: 'Hvad viste prøven om pasformen, og hvad ændrede du bagefter?',
    steps: [
      {title: 'Find kravene', text: 'Vælg hvem skiltet er til, hvor det skal stå, og hvilken tekst det skal vise.', check: 'Hvad skal skiltet kunne i virkeligheden?'},
      {title: 'Mål materialet', text: 'Mål materialetykkelsen sammen med læreren, og skriv målet ned før du tegner foden.', hint: 'Et nominelt mål på 3 mm er kun et forslag, ikke en garanti.', check: 'Hvilket mål skal din samling passe til?'},
      {title: 'Tegn skiltet', text: 'Tilføj en skiltflade, vælg form og sæt bredde og højde. Brug “Behold figurens form”, når proportionerne skal bevares.', target: '#object-width, #object-height', check: 'Er skiltet stort nok til at kunne læses på bordet?'},
      {title: 'Tilføj teksten', text: 'Skriv skiltets navn eller besked, og placér den inden for kanten.', target: '#add-text', check: 'Kan teksten læses fra den side, hvor brugeren står?'},
      {title: 'Byg en U-fod', text: 'Lav et smalt rektangel som fod. Lav derefter et smallere rektangel, der går ud til fodens kant, og vælg begge dele med det smalle øverst. Tryk “Lav hul med øverste figur”.', hint: 'Åbningen skal passe til materialetykkelsen og være åben ud mod kanten.', target: '#subtract', check: 'Kan skiltet gå ned i åbningen?'},
      {title: 'Lav den anden fod', text: 'Kopiér U-foden og placér kopien, så skiltet får støtte i begge sider.', target: '#duplicate', check: 'Står fødderne stabilt under skiltet?'},
      {title: 'Lav prøve og forbedr', text: 'Sæt de fysiske dele til Skær, lav en lille prøvesamling, åbn forhåndsvisningen, og vælg den ændring der bedst forbinder skiltets udseende med funktionen.', target: '#preview-button', check: 'Hvilken ændring kan du begrunde med prøven?'}
    ]
  },
  {
    id: 'box-for-content', title: 'Æske til et bestemt indhold', level: 3,
    intro: 'Design en æske ud fra et konkret indhold, det målte materiale og en samling, der skal afprøves.',
    skill: 'Måling, parametrisk design, materialetykkelse og funktion.',
    reflection: 'Hvilket mål eller krav ændrede din løsning?',
    steps: [
      {title: 'Vælg indholdet', text: 'Vælg den genstand æsken skal rumme, og beslut om der skal være ekstra plads til at tage den ud.', check: 'Hvad skal æsken beskytte eller gøre lettere?'},
      {title: 'Mål genstanden', text: 'Mål bredde, dybde og højde sammen med læreren, og skriv målene ned.', check: 'Hvilket mål er størst, og hvor skal der være luft?'},
      {title: 'Vælg materiale', text: 'Find materialetykkelsen og et forsigtigt forslag til brændingskorrektion med læreren.', hint: 'Værdierne afhænger af materiale og maskine.', check: 'Hvilke mål er sikre, og hvilke skal prøves?'},
      {title: 'Byg med dine mål', text: 'Åbn kassegeneratoren, og lav en kasse med dine mål.', hint: 'Skriv dine mål i vinduet, opdatér forhåndsvisningen, og vælg først “Indsæt i tegningen”, når du har kontrolleret delene. Derefter fortsætter du her.', target: '#box-button', check: 'Passer målene til det, din æske skal rumme?'},
      {title: 'Gør æsken personlig', text: 'Tilføj en tekst eller en enkel figur på én af kassens sider. Vælg Gravér, hvis den skal blive på overfladen.', target: '#add-text', check: 'Er din dekoration inden for kanten og fri af samlingerne?'},
      {title: 'Se alle kassens dele', text: 'Åbn forhåndsvisningen, og kontrollér hvilke dele der bliver skåret og graveret.', target: '#preview-button', check: 'Er alle kassens dele med på arbejdsområdet?'},
      {title: 'Prøv og begrund', text: 'Lav eller aftal en lille fysisk afprøvning, åbn laserforhåndsvisningen, og vælg én ændring eller begrund, hvorfor løsningen er klar.', target: '#preview-button', check: 'Hvilken måling eller prøve støtter din beslutning?'}
    ]
  },
  {
    id: 'place-sign', title: 'Skiltning til et sted på skolen', level: 3,
    intro: 'Design et skilt til en virkelig placering og forbedr det efter en brugers behov.',
    skill: 'Design til andre, mål, læsbarhed, afprøvning og begrundelse.',
    reflection: 'Hvilken feedback førte til din forbedring?',
    steps: [
      {title: 'Undersøg stedet', text: 'Besøg eller forestil dig placeringen, og skriv hvem der skal se skiltet og fra hvilken afstand.', check: 'Hvad skal brugeren kunne gøre efter at have set skiltet?'},
      {title: 'Lav en måleskitse', text: 'Notér den tilgængelige plads og vælg et realistisk forslag til skiltets bredde og højde.', hint: 'Brug pladsen som forslag, og tjek den med læreren.', check: 'Hvilket mål er styret af stedet?'},
      {title: 'Tegn første forslag', text: 'Tilføj en skiltflade og skriv den korteste tekst, der løser opgaven.', target: '[data-add="rect"]', check: 'Er teksten kort nok til at kunne læses hurtigt?'},
      {title: 'Lav et andet forslag', text: 'Lav en tydeligt anderledes udgave ved at ændre komposition, symbol eller størrelse.', target: '#duplicate', check: 'Hvad er den vigtigste forskel mellem forslagene?'},
      {title: 'Vælg laserhandlinger', text: 'Beslut hvilke dele der skal skæres og hvilke der skal graveres, ud fra placering og materiale.', target: '[data-op="engrave"]', check: 'Hvorfor passer denne proces til netop dette skilt?'},
      {title: 'Afprøv læsbarheden', text: 'Vis forslagene til en anden person eller se dem på afstand, og vælg én konkret forbedring.', check: 'Hvad lagde brugeren eller du mærke til først?'},
      {title: 'Forhåndsvis den forbedrede version', text: 'Åbn forhåndsvisningen og tjek kant, tekst og procesmarkeringer, før du beskriver dit valg.', target: '#preview-button', check: 'Hvilken feedback eller observation kan du pege på?'}
    ]
  }
];

export function getLaserGuide(id) {
  return LASER_GUIDES.find(guide => guide.id === id);
}
