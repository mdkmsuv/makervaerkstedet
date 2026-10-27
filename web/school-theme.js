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
