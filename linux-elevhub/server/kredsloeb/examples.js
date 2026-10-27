/* CircuitJS1 text circuits. Kept as plain text so teachers can inspect and edit them. */
window.KREDSLOEB_EKSEMPLER = [
  { id:'kontakt', title:'1 · Kontakt og lampe', summary:'Styr strømmen med en kontakt.', task:'Luk kontakten, og følg strømmen. Hvad sker der, når kontakten åbnes?', circuit:`$ 1 0.000005 10.2 50 5 50
v 112 256 112 128 0 0 0 6 0 0
g 112 256 112 304 0
w 112 128 208 128 0
s 208 128 272 128 0 1 false
181 272 128 272 256 0 293 1 6 0.4 0.4
w 272 256 112 256 0
O 272 128 336 128 0
o 0 64 0 4098 10 0.00009765625 0 2 0 3
` },
  { id:'ohm', title:'2 · Ohms lov', summary:'Mål strømmen, når modstanden ændres.', task:'Kan du forudsige, hvad der sker med strømmen, når modstanden bliver dobbelt så stor?', circuit:`$ 1 0.000005 10.2 50 5 50
v 112 256 112 128 0 0 0 5 0 0
g 112 256 112 304 0
w 112 128 208 128 0
r 208 128 208 256 0 1000
w 208 256 112 256 0
O 208 128 272 128 0
o 0 64 0 4098 10 0.00009765625 0 2 0 3
` },
  { id:'serie', title:'3 · Seriekobling', summary:'To modstande er sat efter hinanden i serie.', task:'Over hvilken modstand er spændingen størst? Hvorfor?', circuit:`$ 1 0.000005 10.2 50 5 50
v 112 256 112 128 0 0 0 9 0 0
g 112 256 112 304 0
w 112 128 208 128 0
r 208 128 208 192 0 1000
r 208 192 208 256 0 2000
w 208 256 112 256 0
O 208 128 272 128 0
O 208 192 272 192 0
o 0 64 0 4098 10 0.00009765625 0 2 0 3
` },
  { id:'parallel', title:'4 · Parallelkobling', summary:'To modstande er sat i hver sin gren af kredsløbet.', task:'Sammenlign strømmen i de to grene. Hvad sker der, hvis du øger værdien på den ene modstand?', circuit:`$ 1 0.000005 10.2 50 5 50
v 112 256 112 128 0 0 0 9 0 0
g 112 256 112 304 0
w 112 128 208 128 0
w 208 128 208 160 0
r 208 160 208 224 0 1000
w 208 224 208 256 0
w 208 128 320 128 0
r 320 128 320 256 0 2000
w 320 256 208 256 0
w 208 256 112 256 0
O 208 160 272 160 0
O 320 128 384 128 0
o 0 64 0 4098 10 0.00009765625 0 2 0 3
` },
  { id:'led', title:'5 · LED og modstand', summary:'En LED er en lysdiode. Hvorfor sidder der en modstand foran den?', task:'Prøv først med 1 kΩ og derefter 100 Ω. Hvad sker der med strømmen?', circuit:`$ 1 0.000005 10.2 50 5 50
v 112 256 112 128 0 0 0 5 0 0
g 112 256 112 304 0
w 112 128 208 128 0
r 208 128 272 128 0 1000
162 272 128 272 256 2 default-led 1 0 0 0.01
w 272 256 112 256 0
O 272 128 336 128 0
o 0 64 0 4098 10 0.00009765625 0 2 0 3
` },
  { id:'kondensator', title:'6 · Kondensatoren', summary:'En kondensator kan lagre elektrisk ladning.', task:'Ændr modstanden. Hvad sker der med den tid, det tager at lade kondensatoren op?', circuit:`$ 1 0.000005 10.2 50 5 50
v 112 256 112 128 0 0 0 5 0 0
g 112 256 112 304 0
w 112 128 208 128 0
r 208 128 208 192 0 1000
c 208 192 208 256 0 0.001 0
w 208 256 112 256 0
O 208 192 272 192 0
o 0 64 0 4098 10 0.00009765625 0 2 0 3
` },
];
