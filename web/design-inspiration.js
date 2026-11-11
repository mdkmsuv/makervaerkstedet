import {PROJECTS,getProject,buildProject} from './design-projects.js';
import {exportSVG,bounds} from './laser-geometry.js';

const LEVELS=[['Gør den til din','Skift navn, symbol og størrelse'],['Byg med former','Sammensæt, kopiér og placér'],['Tegn og kombinér','Lav egne konturer og dele'],['Design og afprøv','Vælg, test og forbedr']];
const STARTER_INTROS=[
  'Vælg en næsten færdig tegning. Ret tekst, symbol og størrelse.',
  'Byg videre med former. Kopiér, drej og placér delene.',
  'Giv konturerne dit eget udtryk. Tegn og kombinér delene.',
  'Design til et formål. Mål, lav en prøve og forbedr din løsning.'
];
const SVG_NS='http://www.w3.org/2000/svg';
const make=(tag,attrs={},text)=>{const n=document.createElement(tag);Object.entries(attrs).forEach(([k,v])=>{if(v!=null)n.setAttribute(k,v)});if(text!=null)n.textContent=text;return n};
const svgMake=(tag,attrs={},text)=>{const n=document.createElementNS(SVG_NS,tag);Object.entries(attrs).forEach(([k,v])=>{if(v!=null)n.setAttribute(k,v)});if(text!=null)n.textContent=text;return n};
function inheritedAttr(node,name,fallback){let n=node;while(n&&n!==node.ownerDocument){const value=n.getAttribute?.(name);if(value)return value;n=n.parentElement}return fallback}
export function previewPathAttributes(path){
  const stroke=inheritedAttr(path,'stroke','#333');
  const attrs={d:path.getAttribute('d'),fill:inheritedAttr(path,'fill','none'),stroke,
    'stroke-width':stroke==='none'?0:1.4,'vector-effect':'non-scaling-stroke'};
  for(const name of ['fill-rule','transform']){
    const value=inheritedAttr(path,name,null);if(value!=null)attrs[name]=value;
  }
  return attrs;
}
export function previewSvg(id,vinyl){const host=svgMake('svg',{viewBox:'0 0 180 120',role:'img','aria-label':'Forhåndsvisning'});try{const project=buildProject(id,vinyl);const source=exportSVG(project,'all',{project:false,workshop:vinyl?'vinyl':'laser',level:3});const doc=new DOMParser().parseFromString(source,'image/svg+xml');const root=doc.documentElement;const w=Number(root.getAttribute('width'))||project.width||180,h=Number(root.getAttribute('height'))||project.height||120;host.setAttribute('viewBox',`0 0 ${w} ${h}`);if(!project.items.length){host.append(svgMake('path',{d:'M100 80L150 50L200 80V140L150 170L100 140ZM100 80L150 110L200 80M150 110V170',fill:'none',stroke:'#c62b2f','stroke-width':2}),svgMake('text',{x:w/2,y:190,'text-anchor':'middle','font-size':11,fill:'#444'},'Vælg mål i generatoren'));return host;}host.append(svgMake('rect',{x:0,y:0,width:w,height:h,fill:'#fff'}));root.querySelectorAll('path').forEach(p=>{const attrs=previewPathAttributes(p);if(attrs.d)host.append(svgMake('path',attrs))})}catch{host.append(svgMake('text',{x:90,y:62,'text-anchor':'middle',fill:'#706b65','font-size':10},'Forhåndsvisning'))}return host}

export function installDesignInspiration(vinyl,callbacks={}){
  document.body.classList.add('desktop-studio',vinyl?'vinyl-studio':'laser-studio');const photo=vinyl?'workshop-vinyl-v1.webp':'workshop-laser-v1.webp';
  const banner=make('section',{class:'studio-welcome','aria-label':'Inspiration til dit værksted'}),image=make('img',{alt:'',width:150,height:90,src:'/assets/'+photo}),copy=make('div');copy.append(make('p',{class:'studio-kicker'},vinyl?'FOLIE':'LASER'),make('h2',{},vinyl?'Tegn et foliemærke':'Tegn til laseren'),make('p',{class:'studio-intro'},vinyl?'Lav et motiv til drikkedunken, penalhuset eller en T-shirt.':'Lav en nøglering, et skilt eller dele, du kan samle.'));const ideas=make('button',{type:'button',class:'secondary studio-ideas'},'Find en idé'),allToolsTop=make('button',{type:'button',class:'quiet studio-all-tools'},'Åbn alle værktøjer');allToolsTop.addEventListener('click',()=>callbacks.selectLevel?.(3));banner.append(image,copy,ideas,allToolsTop);const topline=make('div',{class:'studio-topline'});topline.append(banner,document.querySelector('.level-bar'));document.querySelector('main').prepend(topline);
  document.querySelectorAll('.level-bar [data-level]').forEach((b,i)=>{const l=LEVELS[i];if(l){b.querySelector('strong').textContent=l[0];b.querySelector('small').textContent=l[1]}});
  const dialog=make('dialog',{class:'studio-idea-dialog','aria-labelledby':'studio-idea-title'}),close=make('button',{type:'button',class:'studio-close','aria-label':'Luk projektbiblioteket'},'×'),grid=make('div',{class:'studio-library-grid'}),head=make('div',{class:'studio-library-head'});head.append(make('p',{class:'studio-kicker'},'PROJEKTER DU KAN BYGGE VIDERE PÅ'),make('h2',{id:'studio-idea-title'},vinyl?'Foliemærker med en idé':'Projekter til laserskæring'));const filters=make('div',{class:'studio-library-filters','aria-label':'Filtrér projekter'}),all=make('button',{type:'button',class:'active','data-filter':'all','aria-pressed':'true'},'Alle niveauer');filters.append(all);LEVELS.forEach((l,i)=>filters.append(make('button',{type:'button','data-filter':String(i),'aria-pressed':'false'},l[0])));head.append(filters);const cards=make('div',{class:'studio-project-cards'});grid.append(head,cards);dialog.append(close,grid);document.body.append(dialog);
  let currentFilter='all';const projects=Array.isArray(PROJECTS)?PROJECTS:[];function drawCards(){cards.replaceChildren();projects.filter(p=>(currentFilter==='all'||String(p.level)===currentFilter)&&(!Array.isArray(p.modes)||p.modes.includes(vinyl?'vinyl':'laser'))).forEach(p=>{const card=make('article',{class:'studio-project-card','data-project-id':p.id});card.append(previewSvg(p.id,vinyl));const body=make('div',{class:'studio-project-copy'});body.append(make('p',{class:'studio-project-level'},LEVELS[p.level]?.[0]||'Projekt'),make('h3',{},p.title),make('p',{class:'studio-project-label'},'Det får du'),make('p',{},p.provided||''),make('p',{class:'studio-project-label'},'Du skal gøre'),make('p',{},p.task||''));if(p.skill)body.append(make('p',{class:'studio-project-skill'},`Du øver: ${p.skill}`));const actions=make('div',{class:'studio-project-actions'}),start=make('button',{type:'button',class:'primary'},'Start projekt'),insert=make('button',{type:'button',class:'secondary'},'Indsæt i tegning');start.addEventListener('click',()=>{dialog.close();callbacks.startProject?.(p.id,'replace')});insert.addEventListener('click',()=>{dialog.close();callbacks.startProject?.(p.id,'insert')});actions.append(start,insert);body.append(actions);card.append(body);cards.append(card)});if(!cards.children.length)cards.append(make('p',{class:'studio-empty'},'Der er ikke et projekt til dette værksted på niveauet endnu.'))}
  function selectFilter(value){
    currentFilter=String(value);
    filters.querySelectorAll('button').forEach(b=>{const active=b.dataset.filter===currentFilter;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
    drawCards();
  }
  filters.addEventListener('click',e=>{const b=e.target.closest('button[data-filter]');if(b)selectFilter(b.dataset.filter);});
  const openLibrary=(filter='all')=>{selectFilter(filter);dialog.showModal();};
  ideas.addEventListener('click',()=>openLibrary());close.addEventListener('click',()=>dialog.close());
  const starter=document.querySelector('.starter'),starterTitle=document.getElementById('starter-title'),starterIntro=document.getElementById('starter-intro'),starterCards=document.getElementById('starter-projects');
  let starterLevel=-1;
  document.getElementById('starter-library').addEventListener('click',()=>openLibrary(starterLevel));
  function updateStarters(level){
    if(starterLevel===level)return;
    starterLevel=level;starter.dataset.starterLevel=String(level);
    starterTitle.textContent=LEVELS[level][0];starterIntro.textContent=STARTER_INTROS[level];
    starterCards.replaceChildren();
    projects.filter(p=>p.level===level&&p.modes.includes(vinyl?'vinyl':'laser')).forEach(p=>{
      const button=make('button',{type:'button',class:'studio-starter-card','data-project-id':p.id,'aria-label':`Åbn projekt: ${p.title}`});
      const preview=previewSvg(p.id,vinyl),parts=buildProject(p.id,vinyl).items;
      // Crop the small preview to its drawing so even narrow sidebars show the motif.
      if(parts.length){
        const boxes=parts.map(o=>bounds(o.d)),x=Math.min(...boxes.map(b=>b.x)),y=Math.min(...boxes.map(b=>b.y));
        const w=Math.max(...boxes.map(b=>b.x+b.width))-x,h=Math.max(...boxes.map(b=>b.y+b.height))-y,pad=Math.max(w,h)*.08;
        preview.setAttribute('viewBox',`${x-pad} ${y-pad} ${w+2*pad} ${h+2*pad}`);
      }
      preview.setAttribute('aria-hidden','true');preview.removeAttribute('role');preview.removeAttribute('aria-label');
      const copy=make('span',{class:'studio-starter-copy'});copy.append(make('strong',{},p.title),make('small',{},p.starterHint||p.steps[0]));
      button.append(preview,copy);button.addEventListener('click',()=>callbacks.startProject?.(p.id,'replace'));starterCards.append(button);
    });
  }
  const panel=make('aside',{class:'studio-guidance','aria-label':'Projektvejledning'});
  const objective=make('p',{class:'studio-guidance-objective'});
  const details=make('details',{class:'studio-guidance-steps'});
  const summary=make('summary',{},'Trin, idéer og din eftertanke');
  const steps=make('ol'),skill=make('p',{class:'studio-guidance-skill'});
  const challenge=make('p',{class:'studio-guidance-challenge'});
  const action=make('button',{type:'button',class:'secondary studio-guidance-action'});
  const reflectLabel=make('label',{class:'studio-guidance-reflect'});
  const reflectQuestion=make('span'),reflect=make('textarea',{rows:3,maxlength:1000,placeholder:'Skriv en kort tanke…'});
  const help=make('button',{type:'button',class:'quiet studio-guidance-help'},'Skjul hjælp');
  reflectLabel.append(reflectQuestion,reflect);
  details.append(summary,steps,skill,challenge,action,reflectLabel);
  panel.append(make('h2',{},'Din projektvejledning'),objective,details,help);
  (document.querySelector('.properties-panel')||document.querySelector('.editor'))?.prepend(panel);
  let activeId='',currentLearning=null;
  details.addEventListener('toggle',()=>{
    if(currentLearning&&Boolean(currentLearning.helpHidden)!==!details.open)callbacks.toggleHelp?.(!details.open);
  });
  reflect.addEventListener('input',()=>callbacks.reflect?.(reflect.value));
  help.onclick=()=>callbacks.toggleHelp?.(!currentLearning?.helpHidden);
  function update(state={},level=0){
    updateStarters(level);
    const learning=state.learning;
    const p=learning?.version===1?getProject(learning.id,vinyl):null;
    panel.hidden=!p;currentLearning=p?learning:null;
    if(!p){activeId='';return;}
    const changed=activeId!==learning.id;activeId=learning.id;
    objective.textContent=p.task;objective.title=p.task;
    if(changed){
      steps.replaceChildren(...(p.steps||[]).map(x=>make('li',{},x)));
      skill.textContent=`Du øver: ${p.skill}`;
      challenge.textContent=`Ekstra udfordring: ${p.challenge}`;
      reflectQuestion.textContent=p.reflection;
    }
    details.open=!learning.helpHidden;
    action.hidden=!p.action;
    action.textContent=p.action==='image'?'Prøv med et billede':'Åbn æskegenerator';
    action.onclick=()=>callbacks.projectAction?.(p.action);
    help.textContent=learning.helpHidden?'Vis hjælp':'Skjul hjælp';
    if(changed||document.activeElement!==reflect){
      if(reflect.value!==(learning.reflection||''))reflect.value=learning.reflection||'';
    }
  }
  const helpButton=document.getElementById('help-button');if(helpButton){helpButton.textContent='Hjælp';document.querySelector('.project-actions')?.prepend(helpButton)}return {update,openLibrary};
}
