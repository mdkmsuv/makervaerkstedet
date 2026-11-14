# Installér fra Windows-EXE og Debian-ISO

Kreditering: Klostermarksskolen.

Denne vejledning gælder en færdig leverance med
`Makervaerkstedet-server-Setup.exe`, eventuelt
`Makervaerkstedet-laser_station-Setup.exe`, og en elev-ISO. Kildekode er ikke
en installationspakke. Følg også leverancens README for præcis version,
filplaceringer, prøver og begrænsninger.

## Før du begynder

Serveren skal være Windows x64 med fast IPv4-adresse eller DHCP-reservation.
Elevcomputeren skal være Intel/AMD amd64. Begge skal kunne nå hinanden på det
angivne elevnet. Debian-installationen kræver internet; værkstederne følger
med ISO'en. Brug en frisk testprofil ved en hjemmeprøve.

Kontrollér leverancens signaturer og kvittering. Tag backup før installation
på en computer med data. Ved en VM-prøve skal elevens disk være ny og tom.
ISO'en sletter den valgte disk efter bekræftelse og bruges ikke til almindelig
opdatering af en elevcomputer.

## 1. Installér serveren

1. Start **Makervaerkstedet-server-Setup.exe** inde på den Windows-computer,
   hvor serveren skal køre. Windows beder om administratorgodkendelse ved
   opstart af denne EXE. Godkend selv den valgte installer.
2. Guiden viser **Ny installation**. Viser den **Opdatering fundet**, findes
   der allerede en kendt installation. Stop en planlagt ren prøve her og få
   testgrundlaget kontrolleret.
3. På **Skolens opsætning** indtastes skolens/teststedets navn, serverens
   IPv4-adresse og elevsubnet i CIDR-format. En klargjort VM-prøves README
   angiver dens værdier. Brug egne netoplysninger på fysisk udstyr.
4. På **Computerens rolle** vælges **Server med separat laserstation
   (VM/server)** til en server-VM. **Fælles makerspace-computer med lokal
   laserstation** er det andet valg. En separat laserstation installeres
   senere med sin egen EXE og forbindelsesfil.
5. Vælg **Skolens farvetema** og **Lasermodel**. Brug testens angivne valg
   ved en syntetisk prøve. Dette starter ingen fysisk laser.
6. Gennemse valgene og start installationen. Vent på afslutning og
   efterkontrol; luk ikke VM'en undervejs. Ved fejl bevares data og logs.
7. Åbn skrivebordsgenvejen **Makerværkstedet**, vælg selv en lærerkode på
   mindst otte tegn og kontrollér lærerens panel.

Serveren opretter `school-connection.json` i
`C:\Users\Public\Documents\Elevvaerkstedet`. Det er den offentlige skolefil
til eleverne. Private nøgler og serverdata i ProgramData bliver på serveren.
`laser-station-connection.json` er en anden fil til Windows-laserstationen.

`START.cmd` åbner den udpakkede programmappes særskilte guide. Følg
EXE-forløbet her, når leverancen indeholder EXE'en; guiderne har forskellige
sider og forskelligt tidspunkt for Windows-godkendelsen.

## 2. Installér Debian-eleven

1. Brug den skrevne installations-USB på fysisk udstyr. I Hyper-V tilknyttes
   samme ISO til en NY Gen-2 VM med Secure Boot og én tom disk.
2. Start fra mediet. Vælg **Dansk** ved tastaturspørgsmålet, hvis det vises.
   Netværket skal have internet under Debian-installationen.
3. Kontrollér den viste disks model og størrelse. Sletbekræftelsen står på
   **Nej**; vælg kun **Ja**, når den aftalte, tomme testdisk er vist.
4. Vent, til installeren selv slukker. Fjern USB eller frakobl ISO'en og
   start fra den installerede disk.
5. Første boot tilbyder **Skoleopsætning: tryk S og Enter inden 10 sekunder**.
   Til offlineprøven trykker du Enter eller venter. Den lokale elevflade
   åbner. Skoleopsætningen tilbydes igen ved genstart, så længe eleven ikke
   er tilmeldt.
6. Åbn Laser, tegn en figur, gem, gå til start og genåbn fra projekthylden.
   Prøv Folie og 3D, hvor grafikken understøttes. VM'ens grafik beviser ikke
   fysisk GPU-/WebGL-understøttelse.

## 3. Tilmeld eleven til den nye server

1. Serveren skal køre og kunne nås. Opret et unikt computernavn og en
   engangskode i serverens administration/`Manage-School.cmd`.
   Koden gælder i 30 minutter.
2. På fysisk udstyr kopieres den NYE servers `school-connection.json` til
   en almindelig fil-USB. Installations-USB'en med råt ISO-indhold er et
   andet medie og er ikke en almindelig skrivbar mappe til skolefilen.
3. **Hyper-V kræver et ekstra overførselstrin.** Filen skal gøres læsbar på
   en kendt sti inde i gæsten, efter serveren har oprettet den. Følg
   VM-prøvens README eller få hjælp til denne handling. Skoleopsætningen
   monterer ikke DVD/VHDX automatisk. Den generiske gæst lover heller ikke
   SSH- eller clipboard-adgang; tilknytning af en DVD er ikke nok.
4. Genstart den uindmeldte elev, tryk **S og Enter** ved tilbuddet, og vælg
   skolefilens nummer eller skriv dens fulde, tilgængelige sti.
5. Kontrollér vist skolenavn og adresse. Indtast samme computernavn og
   engangskoden; den vises ikke, mens du skriver. Bekræft med **JA** og vent
   på færdig tilmelding.
6. Kontrollér eleven i lærerens panel. Afprøv efter aftale elevskift,
   gem/genåbn, aflevering og lærerens modtagelse. Aflevering er ikke en
   tilladelse til fysisk produktion.

Ved geninstallation afmeldes det gamle computernavn først i serverens
administration. Genbrug ikke en anden skoles profil eller certifikater.

## Opdatering og fejl

Samme rolle-EXE viser **Opdatering fundet** ved en kendt installation og
bevarer profil, identitet og data. Serverens køer bliver pauset, indtil
læreren fortsætter. Genoptagelse, backupgendannelse og opdatering er
forskellige handlinger. Se [Kom godt i gang](KOM-GODT-IGANG.md).

Hvis et trin fejler, stop dér og notér trinnet og den synlige fejl. Bevar
data og logs. Læs leverancens aktuelle status før et nyt forsøg; en kilde-
eller VM-prøve er ikke en godkendelse af skolens drift eller fysisk hardware.
