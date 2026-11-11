import {NS,el,bounds,imageOutline,closed} from './laser-geometry.js';

// SDX1250 operation manual, p. 17: usable areas, not the physical mat dimensions.
export const MATS={
  standard:{width:296,height:298,label:'Standardmåtte · 30,5 × 30,5 cm'},
  long:{width:296,height:603,label:'Lang måtte · 30,5 × 61 cm'},
};
export function vinylState(state){
  const mat=Object.hasOwn(MATS,state.mat)?state.mat:state.height>298?'long':'standard';
  return {...state,mat,width:MATS[mat].width,height:MATS[mat].height,items:state.items.map(({gear,...o})=>({...o,op:['skip','undecided'].includes(o.op)?'undecided':'cut'}))};
}
export function brotherIssues(state){
  const issues=[],active=state.items,mat=MATS[state.mat];
  if(!active.length)issues.push('Tilføj mindst én figur, der skal skæres.');
  if(active.some(o=>o.op!=='cut'))issues.push('Vælg Skær for de dele, der mangler et valg. Slet de dele, du ikke vil have med.');
  if(!mat||state.width!==mat.width||state.height!==mat.height)issues.push('Vælg en måtte til folieskæreren.');
  for(const o of active){
    const d=imageOutline(o);
    if(!d){issues.push(`Trace “${o.name}”, før figuren kan gemmes.`);continue;}
    const b=bounds(d);
    if(b.x<-.02||b.y<-.02||b.x+b.width>state.width+.02||b.y+b.height>state.height+.02)issues.push(`“${o.name}” ligger uden for skæreområdet. Flyt eller formindsk figuren, eller vælg en større måtte.`);
  }
  return issues;
}
export function brotherNotes(state){
  return state.items.some(o=>o.op==='cut'&&imageOutline(o)&&!closed(imageOutline(o)))?['Åbne streger bliver snit i folien. Luk konturen, hvis du vil skære en hel figur fri.']:[];
}
export function exportBrotherSVG(state){
  const svg=el('svg',{xmlns:NS,version:'1.1',width:state.width+'mm',height:state.height+'mm',viewBox:`0 0 ${state.width} ${state.height}`});
  svg.append(el('title',{},state.name));
  // Flatten image transforms and export the actual contours. No bitmap, frame,
  // live text, process metadata or CSS is sent to the cutter.
  for(const o of state.items.filter(o=>o.op==='cut')){
    const d=imageOutline(o);
    if(!d)throw Error(`Trace “${o.name}”, før figuren kan gemmes.`);
    svg.append(el('path',{d,fill:'none',stroke:'#000000','stroke-width':.1,'fill-rule':o.image?'evenodd':o.fillRule||'nonzero'}));
  }
  return new XMLSerializer().serializeToString(svg);
}

export function configureVinylUI(){
  document.documentElement.dataset.workshop='vinyl';
  document.title='Folieværksted · Makerværkstedet';
  document.querySelector('.brand h1').textContent='Folieværksted';
  document.querySelector('.header-note').textContent='Brother SDX1250 · USB';
  document.querySelector('.properties-panel h2').innerHTML='<span class="step">2</span> Tilpas din figur';
  document.querySelector('#add-gear').hidden=true;
  document.querySelector('[data-level="0"] small').textContent='Former og dit navn';
  document.querySelector('[data-op="engrave"]').hidden=true;
  document.querySelector('[data-op="cut"] small').textContent='Følg figurens konturer';
  document.querySelector('.legend').innerHTML='<i class="cut-dot"></i> Skærelinjer';
  document.querySelector('#image-properties>p').textContent='Figurens konturer bliver skærelinjer i din SVG.';
  document.querySelector('#image-dialog>.muted').textContent='En tyk streg får to konturer. Alle konturer bliver skåret. Justér detaljerne, til du er tilfreds.';
  document.querySelector('#send-button').hidden=true;
  document.querySelector('#export-button').className='secondary';
  document.querySelector('#export-button').textContent='SVG til Brother ↓';
  document.querySelector('#preview-export').textContent='SVG til Brother ↓';
  document.querySelector('.bottom-strip>.muted').textContent='Gem projekt til senere · SVG til Brother til USB-nøglen.';
  document.querySelector('.preview-tabs').hidden=true;
  document.querySelector('.preview-note').textContent='Stregerne viser, hvor kniven skærer. Bogstaver og billedkonturer bliver skåret langs kanterne.';
  document.querySelector('#preview-dialog h2').textContent='Dine skærelinjer';
  document.querySelector('#help-dialog .help-steps').innerHTML='<li><strong>Tegn.</strong> Brug former, tekst eller konturer fra et billede. De røde streger bliver skåret.</li><li><strong>Tjek måtten.</strong> Vælg den rigtige størrelse. Strygefolie skal normalt spejlvendes med Spejl tegningen.</li><li><strong>Gem.</strong> Gem projekt bevarer redigerbar tekst og billeder. SVG til Brother er filen til maskinen.</li>';
  document.querySelector('#help-dialog>.muted').textContent='Kopiér SVG-filen til USB. På SDX1250 vælger du først Hent data og derefter USB. Åbn filen, og tjek mål og placering.';
  const setup=document.createElement('details');setup.id='vinyl-setup';setup.className='vinyl-setup';setup.setAttribute('aria-label','Folieskærer og måtte');
  setup.innerHTML='<summary>Skæremåtte og spejling</summary><label for="vinyl-mat">Din skæremåtte<select id="vinyl-mat"><option value="standard">Standard · 30,5 × 30,5 cm</option><option value="long">Lang · 30,5 × 61 cm</option></select></label><div class="vinyl-mirror"><button id="vinyl-mirror" class="secondary">Spejl tegningen ↔</button><small>Fx til strygefolie · kan fortrydes</small></div>';
  document.querySelector('.board-settings').before(setup);
}
