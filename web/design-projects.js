import { uid, textPath, center, move, scale, shape, bounds, combine } from './laser-geometry.js';

// Project cards are deliberately data-first. Geometry is made only when a card is started,
// after laser-geometry.loadFont() has completed.
const cards = [
  {id:'nameplate', title:'Navneskilt', level:0, modes:['laser'], provided:'Kant, tekstområde og et enkelt symbol.', task:'Skift navn og symbol, og tilpas teksten så den passer.', skill:'Tekst, størrelse og forskellen på kant og overflade.', challenge:'Gør plads til en kort undertitel.', steps:['Skriv et navn.','Vælg eller tegn et symbol.','Tjek at teksten bliver inden for området.'], reflection:'Hvad ændrede du, og hvad bliver skåret eller graveret?'},
  {id:'key-tag', title:'Taske- eller nøglemærke', level:0, modes:['laser'], provided:'Ophængsform med integreret hul og redigerbar tekst.', task:'Lav et mærke med dit navn og et personligt symbol.', skill:'Placering med hensyn til hul og kant.', challenge:'Vælg en anden grundform og undersøg pladsen.', steps:['Læs målene på mærket.','Redigér navnet.','Kontrollér afstand til hullet.'], reflection:'Hvordan beholdt du plads til ophængningen?'},
  {id:'bottle-label', title:'Mærke til drikkedunk', level:0, modes:['vinyl'], provided:'Et enkelt mærke og symbol på en vinylflade.', task:'Tilpas navn og bredde til den målte flade.', skill:'Sammenhæng mellem mål på skærm og genstand.', challenge:'Sammenlign to læsbare tekststørrelser.', steps:['Mål den flade mærket skal sidde på.','Ret navn og størrelse.','Sammenlign forhåndsvisningen med målet.'], reflection:'Hvilken størrelse kan læses på afstand?'},
  {id:'room-sign', title:'Dør- eller rumskilt', level:0, modes:['laser'], provided:'En skiltflade, et tydeligt tekstområde og et lille symbol.', task:'Skriv navnet på rummet, og vælg et symbol der hjælper andre.', skill:'Redigerbar tekst, størrelse og placering inden for en kant.', challenge:'Lav en kort undertitel uden at teksten går ud over skiltet.', steps:['Skriv rummets navn.','Flyt eller tilpas teksten, så den står inden for kanten.','Forklar hvad symbolet fortæller en bruger.'], reflection:'Hvordan gjorde du skiltet let at læse?'},
  {id:'medal', title:'Medalje med navn', level:0, modes:['laser'], provided:'En rund medalje med hul til et bånd, redigerbar tekst og stjerne.', task:'Skriv en titel eller et navn, og gør medaljen personlig.', skill:'Tekst og symbol inden for en rund kant.', challenge:'Lav en ekstra medalje til en anden anledning.', steps:['Redigér teksten i medaljen.','Tilpas stjernen, og behold plads til båndhullet.','Tjek at tekst og symbol bliver inden for den runde kant.'], reflection:'Hvilken ændring gør medaljen mest personlig?'},
  {id:'name-sticker', title:'Navneklistermærke', level:0, modes:['vinyl'], provided:'En enkel klistermærkeform, redigerbar tekst og et symbol.', task:'Skriv et navn eller ord, og tilpas størrelsen til den målte flade.', skill:'Læsbar tekst, enkel kontur og mål på en fysisk genstand.', challenge:'Lav en ekstra version med et andet symbol.', steps:['Mål fladen hvor mærket skal sidde.','Redigér ordet og tilpas størrelsen.','Sammenlign mærkets mål med genstanden.'], reflection:'Hvordan ved du, at teksten kan læses på genstanden?'},
  {id:'pattern-bookmark', title:'Mønsterbogmærke', level:1, modes:['laser'], provided:'Bogmærkets kontur og én graveringsfigur.', task:'Kopiér, drej og fordel figuren til et mønster.', skill:'Kopiering, afstand og rotation.', challenge:'Lav en bevidst variation i gentagelsen.', steps:['Undersøg den første figur.','Lav flere kopier med afstand.','Drej mindst én figur og tjek kanten.'], reflection:'Hvad gør mønsteret regelmæssigt eller uroligt?'},
  {id:'club-logo', title:'Hold- eller klublogo', level:1, modes:['laser','vinyl'], provided:'Tre selvstændige former og redigerbar tekst.', task:'Lav en kompakt og en bred komposition med delene.', skill:'Del og helhed, centrering og gruppering.', challenge:'Lav to udgaver til forskellige anvendelser.', steps:['Flyt de tre former.','Centrér teksten i kompositionen.','Sammenlign en kompakt og en bred udgave.'], reflection:'Hvilke dele skal ses som én helhed?'},
  {id:'workshop-labels', title:'Etiketter til værkstedets kasser', level:1, modes:['vinyl'], provided:'Tre redigerbare etiketter med eksempeltekster.', task:'Lav en ensartet serie til faktiske kasser.', skill:'Genbrug, gentagelse og konsistens.', challenge:'Lad en anden elev afkode symbolerne uden forklaring.', steps:['Find tre kasser der skal mærkes.','Kopiér samme etiketteform.','Tilpas ord og symboler, men behold en fælles stil.'], reflection:'Hvordan kan andre forstå etiketterne hurtigt?'},
  {id:'own-silhouette', title:'Egen silhuet', level:2, modes:['laser','vinyl'], provided:'En lukket silhuet med punkter, du kan flytte.', task:'Skab et dyr, køretøj eller abstrakt motiv med egne valg.', skill:'Geometriske operationer og punktrettelse.', challenge:'Lav to forskellige udtryk med samme grundformer.', steps:['Undersøg silhuettens dele.','Flyt eller redigér konturen.','Lav en variant og sammenlign dem.'], reflection:'Hvilken geometrisk ændring gav størst forskel?'},
  {id:'vinyl-motif', title:'Tegning til foliemotiv', level:2, modes:['vinyl'], provided:'Et enkelt redigerbart vektoreksempel.', task:'Fjern detaljer og vurder de faktiske skærelinjer.', skill:'Bitmap kontra vektor og konturforenkling.', challenge:'Forklar hvorfor en tyk billedstreg kan give to konturer.', steps:['Undersøg de lukkede konturer.','Fjern en detalje uden at ødelægge motivet.','Tjek motivet som skærelinjer.'], reflection:'Hvilke linjer bliver faktisk skåret?', action:'image'},
  {id:'stencil', title:'Genbrugelig stencil', level:2, modes:['laser'], provided:'Ramme og et enkelt motiv som redigerbart vektoreksempel.', task:'Lav eget motiv og forbind indvendige øer med broer.', skill:'Positiv og negativ form samt sammenhæng i materialet.', challenge:'Find broplaceringer der ændrer motivet mindst.', steps:['Find de indvendige øer.','Tegn broer mellem øer og ramme.','Forudsig hvad der bliver tilbage efter udskæring.'], reflection:'Hvorfor er hver bro nødvendig?'},
  {id:'table-sign', title:'Bordskilt med fod', level:3, modes:['laser'], provided:'Skilt og to åbne U-fødder med et nominelt 3 mm prøvespor.', task:'Vælg skiltform og samling, afprøv i materialet og tilpas.', skill:'Materialetykkelse og pasform.', challenge:'Begrund en ændring efter en fysisk prøve.', steps:['Mål materialet sammen med læreren.','Tilpas skilt og fødder.','Lav en lille prøve før det endelige snit.'], reflection:'Hvad viste prøven om pasformen? Målene er kun et udgangspunkt og er ikke en garanti.', contentArea:{x:20,y:12,width:140,height:55}},
  {id:'box-for-content', title:'Æske til et bestemt indhold', level:3, modes:['laser'], provided:'Et opgavekort og adgang til æskegeneratoren.', task:'Bestem mål, placér gravering og afprøv plads og samling.', skill:'Parametrisk design og funktion.', challenge:'Sammenlign to løsninger og begrund materialeforbruget.', steps:['Mål indholdet.','Brug æskegeneratoren med lærerens vejledning.','Afprøv samling og plads på en prøve.'], reflection:'Hvilket mål eller krav ændrede din løsning?', action:'box'},
  {id:'place-sign', title:'Skiltning til et sted på skolen', level:3, modes:['laser','vinyl'], provided:'Åbent opgavekort med plads til elevens egen måleskitse.', task:'Undersøg bruger og placering, lav to forslag og forbedr ét.', skill:'Design til andre og begrundelse.', challenge:'Få feedback fra den person der skal bruge skiltet.', steps:['Mål stedet og tegn din egen skitse.','Lav to forskellige forslag.','Afprøv læsbarhed og forbedr ét.'], reflection:'Hvilken feedback førte til din forbedring?'},
];

const starterHints={
  'nameplate':'Ret navn og symbol', 'key-tag':'Navn og hul til en ring',
  'bottle-label':'Tilpas til din drikkedunk', 'room-sign':'Lav et skilt til dit rum',
  'medal':'Vælg navn og anledning', 'name-sticker':'Dit navn som foliemærke',
  'pattern-bookmark':'Kopiér og drej et mønster', 'club-logo':'Placér former og tekst',
  'workshop-labels':'Lav en serie etiketter', 'own-silhouette':'Flyt punkter i konturen',
  'vinyl-motif':'Forenkl et motiv', 'stencil':'Forbind motivet med broer',
  'table-sign':'Afprøv skilt og fødder', 'box-for-content':'Mål og byg en æske',
  'place-sign':'Design til andre'
};
export const PROJECTS = cards.map(card => ({...card, starterHint:starterHints[card.id], modes:[...card.modes], steps:[...card.steps]}));

export function getProject(id, vinyl=false) {
  const card = cards.find(p => p.id === id);
  if (!card) return undefined;
  const mode = vinyl ? 'vinyl' : 'laser';
  return card.modes.includes(mode) ? {...card, modes:[...card.modes], steps:[...card.steps]} : undefined;
}

const item = (name, d, op, text) => ({id:uid(), name, d, op, fillRule:'nonzero', ...(text ? {text} : {})});
const fittedText = (value, size, maxWidth) => {
  let d = textPath(value, size);
  const b = bounds(d);
  const actualSize = b.width > maxWidth ? size * maxWidth / b.width : size;
  if (actualSize !== size) d = textPath(value, actualSize);
  return {d, size:actualSize};
};
const rect = (w,h) => scale(shape('rect'), w/45, h/30, 0, 0);
const at = (d,x,y) => center(d,x,y);
const text = (value,size,x,y,maxWidth) => {
  const fitted = fittedText(value,size,maxWidth);
  return {d:at(fitted.d,x,y), size:fitted.size};
};
const sampleText = 'DIT NAVN';

function makeProject(card, vinyl) {
  const I = (name,d,laserOp='engrave-fill',meta) => item(name,d,vinyl?'cut':laserOp,meta);
  if (card.id === 'box-for-content') return {width:300,height:200,items:[]};
  if (card.id === 'nameplate') {
    const w=vinyl?150:120,h=vinyl?54:50;
    const fitted = text(sampleText,vinyl?14:13,w/2-15,h/2,vinyl?82:62);
    return {width:vinyl?296:180,height:vinyl?298:120,items:[I('Skiltkant',at(rect(w,h),w/2,h/2),'cut'),I('Navn',fitted.d,vinyl?'cut':'engrave-fill',{value:sampleText,size:fitted.size}),I('Symbol',at(scale(shape('star'),.62,.62,0,0),w-19,h/2),'engrave-fill')]};
  }
  if (card.id === 'key-tag') {
    const w=vinyl?296:180, hole='M54,44 A4,4 0 1 1 54,52 A4,4 0 1 1 54,44 Z';
    const frameWithHole=`M44,32 H116 V64 H44 Z ${hole}`;
    const fitted=text(sampleText,10,92,48,42);
    return {width:w,height:vinyl?298:120,items:[I('Mærke med ophæng',frameWithHole,'cut'),I('Navn',fitted.d,vinyl?'cut':'engrave-fill',{value:sampleText,size:fitted.size})]};
  }
  if (card.id === 'bottle-label') { const fitted=text(sampleText,14,148,70,115); return {width:296,height:298,items:[I('Folieetiket',at(rect(150,48),148,70),'cut'),I('Navn',fitted.d,'cut',{value:sampleText,size:fitted.size}),I('Symbol',at(scale(shape('circle'),.62,.62,0,0),148,112),'cut')]}; }
  if (card.id === 'room-sign') { const fitted=text(sampleText,13,84,52,72); return {width:180,height:120,items:[I('Skiltkant',at(rect(130,54),90,52),'cut'),I('Navn',fitted.d,'engrave-fill',{value:sampleText,size:fitted.size}),I('Symbol',at(scale(shape('circle'),.42,.42,0,0),136,52),'engrave-fill')]}; }
  if (card.id === 'medal') {
    const outline='M60 75a30 30 0 1 1 60 0a30 30 0 1 1 -60 0Z M81 55H99V51H81Z';
    const fitted=text(sampleText,8,90,75,38);
    return {width:180,height:120,items:[I('Medalje med båndhul',outline,'cut'),I('Navn',fitted.d,'engrave-fill',{value:sampleText,size:fitted.size}),I('Stjerne',at(scale(shape('star'),.3,.3,0,0),90,91),'engrave-fill')]};
  }
  if (card.id === 'name-sticker') { const fitted=text(sampleText,14,138,88,96); return {width:296,height:298,items:[I('Klistermærke',at(rect(150,60),148,88),'cut'),I('Navn',fitted.d,'cut',{value:sampleText,size:fitted.size}),I('Symbol',at(scale(shape('star'),.42,.42,0,0),207,88),'cut')]}; }
  if (card.id === 'pattern-bookmark') {
    const b=rect(42,150);
    return {width:180,height:220,items:[I('Bogmærkekant',at(b,90,110),'cut'),I('Mønsterfigur',at(scale(shape('star'),.42,.42,0,0),90,110),'engrave-line')]};
  }
  if (card.id === 'club-logo') { const fitted=text(sampleText,12,110,100,150); return {width:vinyl?296:220,height:vinyl?298:140,items:[I('Form A',at(scale(shape('circle'),.7,.7,0,0),70,45),'engrave-fill'),I('Form B',at(scale(shape('star'),.7,.7,0,0),110,45),'engrave-fill'),I('Form C',at(scale(shape('heart'),.5,.5,0,0),150,45),'engrave-fill'),I('Klubnavn',fitted.d,vinyl?'cut':'engrave-fill',{value:sampleText,size:fitted.size})]}; }
  if (card.id === 'workshop-labels') return {width:296,height:298,items:[0,1,2].flatMap((i)=>{const value=['TRÆ','LIM','VÆRKTØJ'][i],fitted=text(value,9,70+i*82,55,56);return [I(`Etikette ${i+1}`,at(rect(72,34),70+i*82,55),'cut'),I(`Tekst ${i+1}`,fitted.d,'cut',{value,size:fitted.size})]})};
  if (card.id === 'own-silhouette') {
    const silhouette='M-42,18 L-24,-18 L0,-8 L22,-28 L42,8 L18,28 L-12,20 Z';
    return {width:vinyl?296:220,height:vinyl?298:150,items:[I('Silhuet',at(silhouette,110,65),'cut')]};
  }
  if (card.id === 'vinyl-motif') return {width:296,height:298,items:[I('Vektoreksempel',at(scale(shape('heart'),.9,.9,0,0),148,100),'cut')]};
  if (card.id === 'stencil') {
    const outer=at(rect(140,88),110,75);
    const circle=r=>`M${110-r} 75a${r} ${r} 0 1 0 ${2*r} 0a${r} ${r} 0 1 0 ${-2*r} 0Z`;
    const ring=combine([{d:circle(24)},{d:circle(15)}],'subtract');
    const bridge='M108 48H112V102H108Z';
    const openings=combine([{d:ring},{d:bridge}],'subtract');
    return {width:220,height:150,items:[I('Stencil med to broer',outer+' '+openings,'cut')]};
  }
  if (card.id === 'table-sign') {
    const uLeft='M45 72H54.5V90H57.5V72H67V102H45Z', uRight='M113 72H122.5V90H125.5V72H135V102H113Z';
    const fitted=text(sampleText,10,90,32,100);
    return {width:180,height:120,items:[I('Skilt',at(rect(130,48),90,32),'cut'),I('Venstre U-fod',uLeft,'cut'),I('Højre U-fod',uRight,'cut'),I('Eksempeltekst',fitted.d,'engrave-fill',{value:sampleText,size:fitted.size})]};
  }
  if (card.id === 'place-sign') { const fitted=text('HERHEN',16,110,55,130); return {width:vinyl?296:220,height:vinyl?298:140,items:[I('Skiltflade',at(rect(160,64),110,55),'cut'),I('Stednavn',fitted.d,vinyl?'cut':'engrave-fill',{value:'HERHEN',size:fitted.size})]}; }
  return {width:180,height:120,items:[]};
}

export function buildProject(id, vinyl=false) {
  const card = getProject(id, vinyl);
  if (!card) throw new Error('Projektet findes ikke i dette værksted.');
  const built = makeProject(card, vinyl);
  const selectedId = built.items.find(o=>o.text)?.id || built.items[1]?.id || built.items[0]?.id || null;
  // These relationships are semantic metadata for project-specific checks; ids are
  // assigned per build so a fresh project never shares object identities.
  const framed = new Set(['nameplate','key-tag','bottle-label','room-sign','medal','name-sticker','pattern-bookmark','table-sign','place-sign']);
  const learning = {id:card.id,version:1};
  if (framed.has(card.id) && built.items.length > 1) {
    learning.frameId = built.items[0].id;
    const content = card.id === 'bottle-label'
      ? [built.items[1]]
      : card.id === 'table-sign'
        ? built.items.slice(-1)
      : built.items.slice(1);
    learning.contentIds = content.map(o => o.id);
  }
  return {name:card.title, width:built.width, height:built.height, items:built.items, learning, selectedId};
}
