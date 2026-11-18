// This panel displays data, never the remote page or its scripts/links.
import {path} from './laser-geometry.js';

export function appendLibraryItems(state, result, uid, closed, {vinyl=false}={}) {
  // Vinyl cuts each contour, including the inner contours of a compound path.
  const parts = vinyl ? result.items.flatMap(item => {
    if (item.image) return [item];
    const compound = path(item.d);
    const contours = compound.children.map(contour => ({...item, d:contour.pathData}));
    compound.remove();
    return contours;
  }) : result.items;
  if (!parts.length || state.items.length + parts.length > 500) throw Error('Tegningen har for mange figurer.');
  const groupId = parts.length > 1 ? uid() : undefined;
  const items = parts.map((item, index) => ({...item, id:uid(), groupId,
    name: index ? `Del ${index + 1} · ${item.name.slice(0, 49)}` : item.name,
    op:vinyl?'cut':closed(item.d)?'engrave-fill':'engrave-line'}));
  state.items.push(...items);
  return items.map(item=>item.id);
}

export function installLaserLibrary({insert,notify,vinyl=false}) {
  const $ = id=>document.getElementById(id), button=$('library-button');
  if (!button) return;
  const api=vinyl?'/api/folie/library':'/api/laser/library';
  const capabilities=window.ElevhubApp?.capabilities;
  const direct=Boolean(capabilities&&[...capabilities.required,...capabilities.optional].includes('svg-library-v1'));
  const operation=vinyl?'skærelinjer':'gravering';
  if(vinyl)$('library-intro').textContent='Motivet sættes ind som én figur. Brug Skil ad, hvis du vil ændre eller slette en del.';
  const dialog=$('library-dialog'), grid=$('library-grid'), message=$('library-status');
  const retry=document.createElement('button');retry.type='button';retry.className='secondary';retry.textContent='Prøv biblioteket igen';retry.hidden=true;message.after(retry);
  const source=document.createElement('p');source.id='library-source-status';source.setAttribute('role','status');source.setAttribute('aria-live','polite');source.hidden=!direct;message.before(source);
  function sourceStatus(payload){if(!direct)return;source.textContent=payload.cache_state==='stale'?'Gemte motiver fra Skoleværkstedets hjemmeside. Nye motiver kræver forbindelse.':payload.cache_state==='empty'?'Skoleværkstedets hjemmeside · motiverne er ikke hentet på denne computer endnu.':'Motiver fra Skoleværkstedets hjemmeside. Hentede motiver kan bruges uden net.';}
  function unavailable(error){if(!direct)return;source.textContent='Biblioteket er ikke tilgængeligt lige nu. Din tegning er bevaret.';if(dialog.open){message.textContent=error.name==='TimeoutError'?'Biblioteket svarer ikke. Prøv igen.':error.message;retry.hidden=false;$('library-page').textContent='Biblioteket kunne ikke hentes';$('library-prev').disabled=$('library-next').disabled=true;}}
  let enabled=false, version=null, revision=0, controller=new AbortController(), items=[], page=0;
  const urls=new Set();
  function revoke() {
    enabled=false;version=null;revision++;controller.abort();controller=new AbortController();
    button.hidden=!direct;grid.replaceChildren();items=[];
    for(const url of urls)URL.revokeObjectURL(url);urls.clear();
    if(dialog.open&&!direct)dialog.close();
  }
  async function request(path) {
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.any([controller.signal,AbortSignal.timeout(direct?22000:10000)])});
    const payload=await response.json();
    if(!response.ok)throw Error(payload.error||'Biblioteket kunne ikke hentes.');
    sourceStatus(payload);
    return payload;
  }
  async function gate() {
    try {
      const payload=await request(api+'/status');
      if(payload.enabled!==true || !Number.isSafeInteger(payload.version))throw Error(vinyl?'Folie er lukket.':'Laser er lukket.');
      if(!direct&&version!==null&&version!==payload.version)revoke();
      enabled=true;version=payload.version;button.hidden=false;
      return payload.version;
    } catch(error) {revoke();unavailable(error);throw error;}
  }
  async function add(item, cardButton) {
    cardButton.disabled=true;
    try {
      await gate();const run=revision;
      const payload=await request(api+'/file/'+item.id);
      const current=await gate();
      if(!enabled||run!==revision||current!==payload.version)throw Error('Adgangen ændrede sig. Vælg figuren igen.');
      // No awaits between final authoritative check and the synchronous edit.
      insert(payload.svg,item.title);
      message.textContent=item.title+' er tilføjet som '+operation+'. Vælg gerne flere.';
    } catch(error) {notify(error.message,true);} finally {cardButton.disabled=false;}
  }
  async function showPreview(item,img,run) {
    try {
      const payload=await request(api+'/file/'+item.id);
      if(!enabled||run!==revision||payload.version!==version||!img.isConnected)return;
      const url=URL.createObjectURL(new Blob([payload.svg],{type:'image/svg+xml'}));urls.add(url);img.src=url;
    } catch {img.alt='Forhåndsvisning kunne ikke hentes';}
  }
  function render() {
    for(const url of urls)URL.revokeObjectURL(url);urls.clear();grid.replaceChildren();
    grid.scrollTop=0;
    const search=$('library-search').value.trim().toLocaleLowerCase('da'), category=$('library-category').value;
    const filtered=items.filter(item=>(!category||item.category===category)&&item.title.toLocaleLowerCase('da').includes(search));
    const pages=Math.max(1,Math.ceil(filtered.length/12));page=Math.min(page,pages-1);
    $('library-page').textContent=`Side ${page+1} af ${pages} · ${filtered.length} figurer`;
    $('library-prev').disabled=page===0;$('library-next').disabled=page===pages-1;
    const previews=[];
    for(const item of filtered.slice(page*12,page*12+12)) {
      const card=document.createElement('article'),img=document.createElement('img'),name=document.createElement('strong'),addButton=document.createElement('button');
      img.alt=item.title;img.width=100;img.height=100;name.textContent=item.title;
      addButton.type='button';addButton.textContent=vinyl?'Tilføj skæremotiv':'Tilføj figur';addButton.onclick=()=>add(item,addButton);
      card.append(img,name,addButton);grid.append(card);previews.push([item,img,revision]);
    }
    if(!filtered.length){const empty=document.createElement('p');empty.textContent='Ingen figurer matcher. Prøv et andet søgeord eller en anden kategori.';grid.append(empty);}
    // Three bounded workers, no external browser navigation or image requests.
    for(let n=0;n<3;n++)(async()=>{while(previews.length){await showPreview(...previews.shift());}})();
  }
  button.onclick=async()=>{
    if(direct&&!dialog.open)dialog.showModal();retry.hidden=true;
    try {
      await gate();const run=revision;message.textContent='Henter figurer…';if(!dialog.open)dialog.showModal();
      const payload=await request(api);
      if(!enabled||run!==revision||!direct&&payload.version!==version)return;
      if(direct){if(!Number.isSafeInteger(payload.version)||payload.enabled!==true)throw Error('Bibliotekets svar kunne ikke læses.');version=payload.version;}
      items=payload.items;page=0;
      const categories=[...new Set(items.map(item=>item.category))].sort((a,b)=>a.localeCompare(b,'da'));
      $('library-category').replaceChildren(new Option('Alle kategorier',''),...categories.map(category=>new Option(category,category)));
      message.textContent=(direct?(payload.cache_state==='stale'?'Gemte motiver fra Skoleværkstedets hjemmeside. Nye motiver kræver forbindelse. ':'Motiver fra Skoleværkstedets hjemmeside. '):'')+'Figurer tilføjes til din tegning som '+operation+'.';render();$('library-search').focus();
    }catch(error){message.textContent=error.name==='TimeoutError'?'Biblioteket svarer ikke. Din tegning er bevaret. Prøv igen.':error.message;retry.hidden=!direct;notify(message.textContent,true);}
  };
  retry.onclick=()=>button.onclick();
  $('library-search').oninput=$('library-category').onchange=()=>{page=0;render();};
  $('library-prev').onclick=()=>{page--;render();};$('library-next').onclick=()=>{page++;render();};
  dialog.addEventListener('close',()=>{for(const url of urls)URL.revokeObjectURL(url);urls.clear();grid.replaceChildren();});
  gate().catch(()=>{});setInterval(()=>gate().catch(()=>{}),2000);
}
