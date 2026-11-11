(() => {
  const dialog = document.getElementById('maker-challenge');
  const ideas = {
    pawn: {
      tag:'01 / LEG MED FORMER', title:'En spillefigur', image:'maker-game-piece-v1.webp',
      alt:'Små 3D-printede robotfigurer ved et tegnet spillebræt.',
      intro:'Byg en robot eller et monster som brik til et brætspil.',
      steps:['Lav kroppen og hovedet, fx med to kasser.', 'Tilføj øjne, ører eller en hjelm. Gør små dele tykke nok til, at de ikke knækker.', 'Giv figuren en flad bund, så den kan stå.']
    },
    pot: {
      tag:'02 / TIL SKRIVEBORDET', title:'En blyantholder', image:'maker-pencil-pot-v1.webp',
      alt:'En bordeaux 3D-printet blyantholder med geometriske sider.',
      intro:'Lav en holder med plads til dine blyanter og tuscher.',
      steps:['Begynd med en rund klods, en cylinder, fx 6 cm bred.', 'Gør holderen hul i midten, men behold bunden og væggene.', 'Tilføj eventuelt et mønster. Tjek målene med læreren før print.']
    },
    rover: {
      tag:'03 / BYG NOGET, DER BEVÆGER SIG', title:'En bil med hjul', image:'maker-rover-v1.webp',
      alt:'En lille 3D-printet bordeaux bil med fire grove hjul.',
      intro:'Byg en bil, og test den på en rampe.',
      steps:['Tegn bilens karrosseri, altså bilens krop. Det kan fx være 8 cm langt.', 'Tegn hjulene hver for sig. Aftal med læreren, hvordan hjul og aksler skal passe sammen.', 'Saml bilen, og se, hvor langt den kan køre ned ad en rampe.']
    }
  };
  document.querySelectorAll('[data-maker-idea]').forEach(button => {
    button.addEventListener('click', () => {
      const idea = ideas[button.dataset.makerIdea];
      if (!idea) return;
      document.getElementById('maker-challenge-tag').textContent = idea.tag;
      document.getElementById('maker-challenge-title').textContent = idea.title;
      document.getElementById('maker-challenge-intro').textContent = idea.intro;
      const picture = document.getElementById('maker-challenge-image');
      picture.src = '/assets/' + idea.image; picture.alt = idea.alt;
      const list = document.getElementById('maker-challenge-steps');
      list.replaceChildren(...idea.steps.map(text => { const item = document.createElement('li'); item.textContent = text; return item; }));
      dialog.showModal();
    });
  });
  document.getElementById('maker-challenge-close').addEventListener('click', () => dialog.close());
  document.getElementById('maker-challenge-print').addEventListener('click', () => {
    dialog.close();
    document.getElementById('lav-print').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
    const upload = document.getElementById('dropzone');
    (upload.hidden ? document.getElementById('lav-print') : upload).focus({preventScroll:true});
  });
})();
