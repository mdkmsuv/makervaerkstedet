'use strict';
(async()=>{
  try{
    const response=await fetch('/api/school-appearance',{cache:'no-store',headers:{Accept:'application/json'}});
    if(!response.ok)return;
    const appearance=await response.json();
    if(!appearance||!['red','yellow','green'].includes(appearance.theme)||typeof appearance.school_name!=='string')return;
    // A stored local skin carries its own exact colours and typography.
    if(!appearance.style)document.documentElement.dataset.schoolTheme=appearance.theme;
    document.querySelectorAll('[data-school-name]').forEach(node=>{node.textContent=appearance.school_name;});
    document.querySelectorAll('[data-school-footer]').forEach(node=>{node.textContent=appearance.school_name+' · Makerværkstedet · Udviklet af Mathias Kusk Damgaard · Kreditering: Klostermarksskolen';});
    if(appearance.style&&appearance.style.website_css==='/school-appearance/website.css'){
      const link=document.createElement('link'); link.rel='stylesheet'; link.href=appearance.style.website_css; document.head.appendChild(link);
    }
    if(appearance.style&&appearance.style.logo==='/school-appearance/logo'){
      if(!document.querySelector('.school-logo')){
        const brand=document.querySelector('.brand');
        if(brand){const logo=document.createElement('img');logo.className='school-logo';brand.prepend(logo);}
      }
      document.querySelectorAll('.school-logo').forEach(node=>{node.src=appearance.style.logo; node.alt=appearance.school_name; node.hidden=false;});
    }
    document.title=document.title.replace(/\s*[·/]\s*Klostermarksskolen\s*$/i,' · '+appearance.school_name);
  }catch{}
})();

// Opened from the laser computer's start page (#forside=<port>): show a way back,
// so the laser station, 3D print and administration feel like one entrance.
(() => {
  'use strict';
  try {
    const opened = /^#forside=(\d{2,5})$/.exec(location.hash);
    if (opened) {
      sessionStorage.setItem('makervaerkstedet-forside', opened[1]);
      history.replaceState(null, '', location.pathname + location.search);
    }
    const port = sessionStorage.getItem('makervaerkstedet-forside') || '';
    if (!/^\d{2,5}$/.test(port) || document.getElementById('forside-retur')) return;
    const bar = document.createElement('div'), back = document.createElement('a');
    bar.id = 'forside-retur';
    // Only this computer's own start page can be the target.
    back.href = `http://127.0.0.1:${port}/makervaerkstedet.html`;
    back.textContent = '\u2190 Tilbage til forsiden';
    Object.assign(bar.style, {display: 'flex', justifyContent: 'flex-end', padding: '10px 5%', background: '#fff', borderBottom: '1px solid #e1e1e1'});
    Object.assign(back.style, {border: '1px solid #c62b2f', borderRadius: '2px', color: '#8c1d24', background: '#fff', padding: '10px 18px', textDecoration: 'none', font: '15px Metrophobic,Helvetica,Arial,sans-serif'});
    bar.append(back);
    document.body.prepend(bar);
  } catch { /* without session storage the button is simply not shown */ }
})();
