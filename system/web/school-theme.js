'use strict';
(async()=>{
  try{
    const response=await fetch('/api/school-appearance',{cache:'no-store',headers:{Accept:'application/json'}});
    if(!response.ok)return;
    const appearance=await response.json();
    if(!appearance||!['red','yellow','green'].includes(appearance.theme)||typeof appearance.school_name!=='string')return;
    // A stored local skin carries its own exact colours and typography.
    // A page may pin its public theme (data-school-theme-fixed) and still take private colours.
    const root=document.documentElement;
    if(!appearance.style&&!root.hasAttribute('data-school-theme-fixed'))root.dataset.schoolTheme=appearance.theme;
    // Optional private school colours (both or neither) win over every theme rule.
    // --school-brand* exist only then, so surfaces that stay red in the public
    // themes keep exactly that look without private colours.
    const colour=/^#[0-9a-f]{6}$/;
    if(typeof appearance.brand==='string'&&typeof appearance.brand_dark==='string'
       &&colour.test(appearance.brand)&&colour.test(appearance.brand_dark)){
      for(const name of ['--brand','--school-brand'])root.style.setProperty(name,appearance.brand);
      for(const name of ['--brand-dark','--school-brand-dark'])root.style.setProperty(name,appearance.brand_dark);
      // Light tint for selected and hover states, so no pink remains beside a
      // school colour that is not red.
      for(const name of ['--brand-soft','--school-brand-soft'])root.style.setProperty(name,'color-mix(in srgb, '+appearance.brand+' 8%, #fff)');
    }
    if(appearance.style&&appearance.style.website_css==='/school-appearance/website.css'){
      const link=document.createElement('link'); link.rel='stylesheet'; link.href=appearance.style.website_css; document.head.appendChild(link);
    }
    function applyIdentity(){
    document.querySelectorAll('[data-school-name]').forEach(node=>{node.textContent=appearance.school_name;});
    document.querySelectorAll('[data-school-footer]').forEach(node=>{node.textContent=appearance.school_name+' · Makerværkstedet · Udviklet af Mathias Kusk Damgaard · Kreditering: Klostermarksskolen';});
    if(appearance.style&&appearance.style.logo==='/school-appearance/logo'){
      if(!document.querySelector('.school-logo')){
        const brand=document.querySelector('.brand,.brand-block,.creative-app-brand');
        if(brand){const logo=document.createElement('img');logo.className='school-logo';brand.prepend(logo);}
      }
      document.querySelectorAll('.school-logo').forEach(node=>{node.src=appearance.style.logo; node.alt=appearance.school_name; node.hidden=false;});
      document.querySelectorAll('.brand .brand-mark,.brand-block .brand-mark,.creative-app-brand .brand-mark').forEach(node=>{node.hidden=true;});
    }
    }
    applyIdentity();
    document.addEventListener('elevhub:identity-ready',applyIdentity);
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
    Object.assign(back.style, {border: '1px solid var(--school-brand, #c62b2f)', borderRadius: '2px', color: 'var(--school-brand-dark, #8c1d24)', background: '#fff', padding: '10px 18px', textDecoration: 'none', font: '15px Metrophobic,Helvetica,Arial,sans-serif'});
    bar.append(back);
    document.body.prepend(bar);
  } catch { /* without session storage the button is simply not shown */ }
})();
