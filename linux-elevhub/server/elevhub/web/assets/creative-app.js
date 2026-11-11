(() => {
  const name=document.currentScript.dataset.workshop,info=window.CreativeWorkshops?.[name];if(!info)return;
  document.body.dataset.creativeApp=name;
  const base=new URL('.',document.currentScript.src);
  const asset=n=>new URL(n,base).href;
  const header=document.createElement('header');header.id='creative-app-header';
  header.innerHTML='<div class="creative-app-brand"><span class="brand-mark" aria-hidden="true"></span><div><small data-school-name>Makerværkstedet / VÆRKSTEDET</small><strong></strong></div></div><p></p><button type="button">Idéer & hjælp <span aria-hidden="true">↗</span></button>';
  header.querySelector('strong').textContent=info.name;header.querySelector('p').textContent=info.tag;
  document.body.prepend(header);
  const combineProjectBar=()=>{const bar=document.getElementById('elevhub-project-bar');if(!bar)return false;header.insertBefore(bar,header.querySelector('button'));header.querySelector(':scope > p').hidden=true;document.body.classList.add('creative-project-header');return true;};
  if(!combineProjectBar()){const barObserver=new MutationObserver(()=>{if(combineProjectBar())barObserver.disconnect();});barObserver.observe(document.body,{childList:true});}
  const dialog=document.createElement('dialog');dialog.id='creative-app-help';dialog.setAttribute('aria-labelledby','creative-app-title');
  dialog.innerHTML='<button type="button" class="creative-app-close" aria-label="Luk idéerne" autofocus>×</button><div class="creative-app-help-grid"><img class="creative-app-photo" alt=""><div class="creative-app-help-copy"><p class="creative-app-eyebrow">IDÉER TIL PROJEKTER</p><h2 id="creative-app-title"></h2><p class="creative-app-intro"></p><details class="creative-first-steps"><summary>Kom i gang</summary><ol></ol></details><div class="creative-app-ideas"></div><p class="creative-app-tip"></p><button type="button" class="creative-app-return">Luk</button></div></div>';
  // Some embedded editors consume Escape. Keep our modal independently keyboard accessible.
  dialog.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();dialog.close();}},true);
  info.steps.forEach(([title,text])=>{const li=document.createElement('li');const strong=document.createElement('strong');strong.textContent=title;const p=document.createElement('p');p.textContent=text;li.append(strong,p);dialog.querySelector('ol').append(li);});
  dialog.querySelector('.creative-app-photo').src=asset(info.image);dialog.querySelector('.creative-app-photo').alt='Inspiration til '+info.name;dialog.querySelector('h2').textContent=info.title;dialog.querySelector('.creative-app-intro').textContent=info.intro;dialog.querySelector('.creative-app-tip').textContent=info.tip;
  info.ideas.forEach(([title,text],i)=>{const d=document.createElement('details');d.open=i===0;const s=document.createElement('summary');s.textContent=title;const p=document.createElement('p');p.textContent=text;d.append(s,p);dialog.querySelector('.creative-app-ideas').append(d);});
  document.body.append(dialog);header.querySelector(':scope > button').addEventListener('click',()=>dialog.showModal());dialog.querySelector('.creative-app-close').addEventListener('click',()=>dialog.close());dialog.querySelector('.creative-app-return').addEventListener('click',()=>dialog.close());
  if(name==='datatraener'){
    const simplifyWelcome=()=>{
      const replacements={
        'Gør-det-selv machine learning':'Kan computeren kende bevægelsen?',
        "Velkommen til 'ultra:bit datatræneren'. Eksperimentér og leg med machine learning og byg dine første machine learning-modeller – kom i gang her!":'Vælg to bevægelser. Vis dem flere gange med din micro:bit. Så prøver computeren at kende forskel.',
        "Hjemmesiden benytter bluetooth, accelerometer, LED'er, knapper og lyd fra BBC micro:bit'en. For at hjemmesiden kan tilgå disse, skal der overføres et program til BBC micro:bit'en. Det downloades første gang, du tilslutter BBC micro:bit'en med USB. Efterfølgende kan du blot tilslutte med bluetooth uden at bruge USB-kablet.":'Første gang skal din micro:bit have et lille program. Tilslut den med USB, og få hjælp fra din lærer. Bagefter kan den sende dine bevægelser trådløst med Bluetooth.',
        'Du har ikke tilkoblet en BBC micro:bit.':'Din micro:bit er ikke tilsluttet endnu.',
        'Gør dette vha. knappen nedenfor':'Læreren kan hjælpe med tilslutningen.'
      };
      document.querySelectorAll('main p').forEach(p=>{if(p.children.length===0&&replacements[p.textContent.trim()])p.textContent=replacements[p.textContent.trim()];});
    };
    const addMission=()=>{const sidebar=document.querySelector('main.h-screen > .min-w-75 > div');if(!sidebar)return false;
      if(!sidebar.querySelector('.creative-data-mission')){const box=document.createElement('section');box.className='creative-data-mission';const image=document.createElement('img');image.src=asset(info.image);image.alt='En håndbevægelse med en lille bevægelsessensor.';const text=document.createElement('p');text.textContent='Optag to forskellige bevægelser, og træn modellen.';box.append(image,text);sidebar.append(box);}return true;};
    simplifyWelcome();addMission();
    const observer=new MutationObserver(()=>{simplifyWelcome();addMission();});observer.observe(document.body,{childList:true,subtree:true});
  }
})();
