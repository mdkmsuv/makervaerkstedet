// This panel displays data, never the remote page or its scripts/links.
export function appendLibraryItems(state, result, uid, closed, {vinyl=false}={}) {
  if (!result.items.length || state.items.length + result.items.length > 500) throw Error('Tegningen har for mange figurer.');
  const group = uid();
  const items = result.items.map(item => ({...item, id:uid(), group,
    op:vinyl?'cut':closed(item.d)?'engrave-fill':'engrave-line'}));
  state.items.push(...items);
  return items.map(item=>item.id);
}

export function installLaserLibrary({insert,notify,vinyl=false}) {
  const $ = id=>document.getElementById(id), button=$('library-button');
  if (!button) return;
  const api=vinyl?'/api/folie/library':'/api/laser/library';
  const operation=vinyl?'skærelinjer':'gravering';
  if(vinyl)$('library-intro').textContent='Vælg et motiv fra Skoleværkstedet. Det tilføjes som skærelinjer på måtten. Din tegning bliver bevaret.';
  const dialog=$('library-dialog'), grid=$('library-grid'), message=$('library-status');
  let enabled=false, version=null, revision=0, controller=new AbortController(), items=[], page=0;
  const urls=new Set();
  function revoke() {
    enabled=false;version=null;revision++;controller.abort();controller=new AbortController();
    button.hidden=true;grid.replaceChildren();items=[];
    for(const url of urls)URL.revokeObjectURL(url);urls.clear();
    if(dialog.open)dialog.close();
  }
  async function request(path) {
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.any([controller.signal,AbortSignal.timeout(10000)])});
    const payload=await response.json();
    if(!response.ok)throw Error(payload.error||'Biblioteket kunne ikke hentes.');
    return payload;
  }
  async function gate() {
    try {
      const payload=await request(api+'/status');
      if(payload.enabled!==true || !Number.isSafeInteger(payload.version))throw Error(vinyl?'Folie er lukket.':'Laser er lukket.');
      if(version!==null&&version!==payload.version)revoke();
      enabled=true;version=payload.version;button.hidden=false;
      return payload.version;
    } catch(error) {revoke();throw error;}
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
    try {
      await gate();const run=revision;message.textContent='Henter figurer…';dialog.showModal();
      const payload=await request(api);
      if(!enabled||run!==revision||payload.version!==version)return;
      items=payload.items;page=0;
      const categories=[...new Set(items.map(item=>item.category))].sort((a,b)=>a.localeCompare(b,'da'));
      $('library-category').replaceChildren(new Option('Alle kategorier',''),...categories.map(category=>new Option(category,category)));
      message.textContent='Figurer tilføjes til din tegning som '+operation+'.';render();$('library-search').focus();
    }catch(error){message.textContent=error.message;notify(error.message,true);}
  };
  $('library-search').oninput=$('library-category').onchange=()=>{page=0;render();};
  $('library-prev').onclick=()=>{page--;render();};$('library-next').onclick=()=>{page++;render();};
  dialog.addEventListener('close',()=>{for(const url of urls)URL.revokeObjectURL(url);urls.clear();grid.replaceChildren();});
  gate().catch(()=>{});setInterval(()=>gate().catch(()=>{}),2000);
}
