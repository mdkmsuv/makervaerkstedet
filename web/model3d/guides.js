// Idékort: frivillige trin-for-trin-vejledninger. De viser vej og fremhæver knapper; de bygger ikke modellen for eleven.
// `done` får et lille overblik over modellen og fortæller, om trinnet ser ud til at være klaret.
const near = (value, goal) => Math.abs(value - goal) < .6;
const sized = (ctx, kind, size) => ctx.parts.some(part => part.kind === kind && size.every((goal, i) => goal === null || near(part.size[i], goal)));
export const GUIDES = [
  {
    id: 'navneskilt', title: 'Navneskilt med hul', minutes: 10,
    intro: 'En lille brik med dit navn og et hul til en nøglering.',
    steps: [
      {title: 'Lav brikken', text: 'Klik på Kasse.', target: '[data-add="box"]:not([data-mode])', done: ctx => ctx.count('box') > 0},
      {title: 'Gør brikken lang', text: 'Klik på det lille skilt L ved kassen. Skriv 70, og tryk Enter.', hint: 'L betyder længde.', target: '.measure[data-axis="L"]', done: ctx => sized(ctx, 'box', [70, null, null])},
      {title: 'Gør brikken smal', text: 'Klik på skiltet B. Skriv 26, og tryk Enter.', hint: 'B betyder bredde.', target: '.measure[data-axis="B"]', done: ctx => sized(ctx, 'box', [70, 26, null])},
      {title: 'Gør brikken flad', text: 'Klik på skiltet H. Skriv 4, og tryk Enter.', hint: 'H betyder højde.', target: '.measure[data-axis="H"]', done: ctx => sized(ctx, 'box', [70, 26, 4])},
      {title: 'Skriv dit navn', text: 'Lad brikken være valgt, og klik på Tekst. Skriv dit navn med det samme, og tryk Enter.', target: '[data-add="text"]', done: ctx => ctx.count('text') > 0},
      {title: 'Lav hullet', text: 'Klik på Rundt hul. Klik på skiltet L, skriv 6 og tryk Enter. Hullet bliver rundt og lille.', hint: 'Et hul er stribet og blåt. Det skærer først, når du trykker Skær hullet ud.', target: '[data-add="cylinder"][data-mode="hole"]', done: ctx => ctx.parts.some(part => part.mode === 'hole' && near(part.size[0], 6))},
      {title: 'Flyt hullet ind', text: 'Træk hullet hen til enden af brikken, hvor der er plads ved siden af navnet. Det skal ligge helt inde på brikken.', hint: 'Klik på Top på terningen for at se ovenfra.', target: '.view-cube'},
      {title: 'Skær hullet ud', text: 'Tryk på Skær hullet ud.', target: '#cut-holes', done: ctx => ctx.parts.length > 0 && !ctx.parts.some(part => part.mode === 'hole')},
      {title: 'Hent din fil', text: 'Tryk på Hent STL. Filen får navnet fra din tekst.', target: '#download', check: 'Kan du se hullet hele vejen igennem?'},
    ],
  },
  {
    id: 'terning', title: 'Terning med hul', minutes: 5,
    intro: 'En terning med et rundt hul lige igennem midten.',
    steps: [
      {title: 'Lav terningen', text: 'Klik på Kasse.', target: '[data-add="box"]:not([data-mode])', done: ctx => ctx.count('box') > 0},
      {title: 'Lav hullet', text: 'Klik på Rundt hul. Klik på skiltet L, skriv 8 og tryk Enter.', target: '[data-add="cylinder"][data-mode="hole"]', done: ctx => ctx.parts.some(part => part.mode === 'hole' && near(part.size[0], 8))},
      {title: 'Vælg begge', text: 'Tryk på Vælg alle, så både terning og hul er valgt.', target: '#select-all', done: ctx => ctx.selected >= 2},
      {title: 'Sæt hullet i midten', text: 'Tryk på Sæt midt på. Det er knappen med tre kugler oven på hinanden.', target: '#center'},
      {title: 'Skær hullet ud', text: 'Tryk på Skær hullet ud.', target: '#cut-holes', done: ctx => ctx.parts.length > 0 && !ctx.parts.some(part => part.mode === 'hole')},
      {title: 'Hent din fil', text: 'Tryk på Hent STL, og giv modellen et navn.', target: '#download', check: 'Kan du kigge gennem hullet, når du drejer modellen?'},
    ],
  },
  {
    id: 'snemand', title: 'Snemand', minutes: 10,
    intro: 'Tre kugler oven på hinanden, med næse og hat.',
    steps: [
      {title: 'Lav tre kugler', text: 'Klik på Kugle tre gange.', target: '[data-add="sphere"]', done: ctx => ctx.count('sphere') >= 3},
      {title: 'Gør dem forskellige', text: 'Klik på den første kugle. Klik på skiltet L, og skriv 30. Giv på samme måde den næste kugle L = 20 og den sidste L = 14.', hint: 'En kugle bliver ved med at være rund.', target: '#workspace', done: ctx => sized(ctx, 'sphere', [30, null, null]) && sized(ctx, 'sphere', [14, null, null])},
      {title: 'Stabl kuglerne', text: 'Tryk på Vælg alle, og tryk derefter på Sæt midt på (de tre kugler). Så står kuglerne oven på hinanden med den største nederst.', hint: 'Du kan også trække en kugle hen på en anden.', target: '#select-all'},
      {title: 'Sæt en næse på', text: 'Træk Kegle fra formerne hen på midten af hovedets forside, den side der vender mod dig og mod ordet FORSIDE.', hint: 'Slipper du keglen på toppen, bliver den en hat. Fortryd og prøv igen lidt længere nede.', target: '[data-add="cone"]', done: ctx => ctx.count('cone') >= 1},
      {title: 'Sæt en hat på', text: 'Træk Kegle op på toppen af hovedet.', target: '[data-add="cone"]', done: ctx => ctx.count('cone') >= 2},
      {title: 'Giv den farver', text: 'Klik på en kugle, hold Shift nede og klik på de andre kugler. Klik på hvid. Giv næse og hat hver sin farve.', target: '#color-row'},
      {title: 'Hent din fil', text: 'Tryk på Hent STL, og kald modellen snemand.', target: '#download', check: 'Hænger alle delene sammen?'},
    ],
  },
  {
    id: 'spillebrik', title: 'Spillebrik', minutes: 5,
    intro: 'En brik til et brætspil: en fod og et hoved.',
    steps: [
      {title: 'Lav foden', text: 'Klik på Cylinder.', target: '[data-add="cylinder"]:not([data-mode]):not([data-sides])', done: ctx => ctx.count('cylinder') > 0},
      {title: 'Gør foden flad', text: 'Klik på det lille skilt H ved cylinderen. Skriv 5, og tryk Enter.', hint: 'H betyder højde.', target: '.measure[data-axis="H"]', done: ctx => sized(ctx, 'cylinder', [null, null, 5])},
      {title: 'Lav kroppen', text: 'Træk Kegle fra formerne op på toppen af foden.', target: '[data-add="cone"]', done: ctx => ctx.count('cone') > 0},
      {title: 'Lav hovedet', text: 'Træk Kugle op på toppen af keglen.', target: '[data-add="sphere"]', done: ctx => ctx.count('sphere') > 0},
      {title: 'Giv den en farve', text: 'Tryk på Vælg alle, og klik på en farve.', target: '#select-all'},
      {title: 'Hent din fil', text: 'Tryk på Hent STL, og kald modellen spillebrik.', target: '#download', check: 'Kan brikken stå selv på foden?'},
    ],
  },
];
