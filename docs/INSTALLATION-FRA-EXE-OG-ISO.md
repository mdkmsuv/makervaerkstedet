# Installér fra Windows-EXE og Debian-ISO

Kreditering: Klostermarksskolen.

Denne vejledning gælder en færdig leverance med
`Makervaerkstedet-server-Setup.exe`, eventuelt
`Makervaerkstedet-laser_station-Setup.exe`, og en elev-ISO. Kildekode er ikke
en installationspakke. Følg også leverancens README for præcis version,
filplaceringer, prøver og begrænsninger.

Vælg først, hvad du skal sætte op:

- **Alt på én Windows-computer:** Følg afsnit 1 og vælg **Alt på én computer**.
  Hovedsystem og lokal laserstation er med i server-EXE'en. Du behøver ingen
  separat servercomputer eller Debian-installation til denne opsætning.
- **Server og laserstation på hver sin computer:** Følg afsnit 1 på serveren
  og derefter **Separat laserstation på Windows** på lasercomputeren.
- **Debian-elevcomputere:** Sæt først hovedsystemet op i en af de to
  Windows-opsætninger. Følg derefter afsnit 2 og 3 på elevcomputerne.

## Vælg guiden til den leverede version

| Leverance | Første elevstart |
|---|---|
| Eksisterende samlet EXE/ISO med versionsslutning `.2` | De medfølgende værksteder kan åbnes lokalt før tilmelding; følg leverancens egen README og den nedenstående manuelle prøve |
| Ny modulær basis | Start, Internet og projekthylde findes uden valgfrie apps; tilmeld først og lad skolens godkendte standardsæt/installationsordrer blive udført |

Den modulære kilde har egne apppakker og en tom basis. Det ændrer ikke
en tidligere skrevet ISO eller USB. Brug aldrig en gammel `.2`-kvittering
som bevis for, at den nye modulære kandidat er installeret eller bootet.
Leverancens README skal angive kildecommit, signaturer, pakkeversioner og
faktisk native/ISO-prøve. Denne guide dokumenterer ikke i sig selv nogen
ny installation.

En manuel prøve af den eksisterende `.2`-leverance hører til netop
denne leverance og dens medier. En udsat prøve genoptages særskilt; en
ny modulær bygning må ikke overskrive dens klargjorte medier eller maskiner.

## Før du begynder

Serveren skal være Windows x64 med fast IPv4-adresse eller DHCP-reservation.
Elevcomputeren skal være Intel/AMD amd64. Begge skal kunne nå hinanden på det
angivne elevnet. Debian-installationen kræver internet. Den eksisterende
samlede ISO medbringer værksteder; den modulære basis installerer apps
efter tilmelding. Brug en frisk testprofil ved en hjemmeprøve.

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
3. På **Computerens rolle** vælges **Server · laserstationen er på en anden
   computer** til en separat server eller server-VM. Vælg **Alt på én computer
   · server og laserstation sammen**, hvis denne Windows-computer skal gøre
   begge dele.
4. På **Skolens opsætning** indtastes skolens/teststedets navn, **denne
   computers** faste IPv4-adresse og elevcomputernes netværk, fx
   `192.0.2.0/24` (dokumentationseksempel). Få netoplysningerne fra den IT-ansvarlige. En klargjort
   VM-prøves README angiver dens værdier. Fejl i formatet forklares, og fokus
   flyttes til feltet, så det kan rettes før installationen. Ældre EXE'er kan vise rolle og netværk
   i omvendt rækkefølge; følg den valgte leverances README.
5. Vælg **Skolens farvetema** og **Lasermodel**. Brug testens angivne valg
   ved en syntetisk prøve. Dette starter ingen fysisk laser.
6. På **Klar til at installere** kontrolleres opsummeringen. Brug **Tilbage**,
   hvis et valg skal ændres, eller **Installer** for at begynde. Efter
   udpakningen sætter guiden systemet op og kontrollerer opstarten. Denne
   fase kan tage nogle minutter; vent, mens markeringen bevæger sig.
7. Fortsæt først, når siden siger **Makerværkstedet er klar**. Vælg **Åbn
   lærerpanel**, eller brug skrivebordsgenvejen **Makerværkstedet**. Vælg selv
   en lærerkode på mindst otte tegn og kontrollér lærerens panel.
8. **Alt på én computer:** Vælg **Åbn laserstation** på afslutningssiden eller
   genvejen **Elevhub Laserstation** på skrivebordet. Log ind med samme
   lærerkode. Der skal ikke køres en separat laserstations-EXE på denne pc.
   **Separat lasercomputer:** Brug **Vis forbindelsesfil** og fortsæt nedenfor.

Serveren opretter `school-connection.json` i
`C:\Users\Public\Documents\Elevvaerkstedet`. Det er den offentlige skolefil
til eleverne. Private nøgler og serverdata i ProgramData bliver på serveren.
`laser-station-connection.json` er en anden fil til Windows-laserstationen.
Hvis filen allerede findes, får den nye eksport en dato i navnet. Brug den
fil, afslutningssiden peger på, og hold hver skoles forbindelsesfiler adskilt.

`START.cmd` åbner den udpakkede programmappes særskilte guide. Følg
EXE-forløbet her, når leverancen indeholder EXE'en; guiderne har forskellige
sider og forskelligt tidspunkt for Windows-godkendelsen.

## Separat laserstation på Windows

Dette afsnit gælder, når laserstationen og hovedsystemet er på hver sin
computer. Hovedsystemet skal allerede være sat op og kunne nås fra lasercomputeren.
Lasercomputeren skal være på det elevnet, som blev angivet i serverguiden;
serverens firewall giver adgang fra dette net.

1. Kopiér den nye `laser-station-connection.json` fra serveren til
   lasercomputeren. Brug serverguidens **Vis forbindelsesfil**; filnavnet kan
   have en dato. Filen indeholder den offentlige CA og stationstilknytning,
   aldrig serverens private nøgle.
   Tag også filen med samme navn og endelsen `.fingerprint.txt` fra serveren.
   Serverens færdigside viser det samme SHA-1-aftryk. Brug værdien fra den
   kendte server som reference, når Windows spørger om certifikatet.
2. Start **Makervaerkstedet-laser_station-Setup.exe** på lasercomputeren som
   den Windows-bruger, der skal bruge stationen. Det er denne brugers
   installation; der skal ikke installeres en ny server.
3. På **Skolens laserforbindelse** vælges filen med **Gennemse**. Kontrollér
   filen i opsummeringen og vælg **Installer**. En kendt station viser i
   stedet **Opdatering fundet** og bevarer sin forbindelse og gemte plader.
   Under opsætningen kan Windows vise **Sikkerhedsadvarsel** for skolens lokale
   CA-certifikat. Vinduet kan ligge bag guiden, som venter på dit svar.
   Kontrollér med skolens IT, at fingeraftrykket tilhører forbindelsesfilens
   certifikat, og vælg **Ja**. Vent derefter på guidens afslutning.
   Er installationen tidligere afbrudt, viser samme EXE **Genoptag installationen**.
   Den genbruger den journalførte forbindelse og kræver præcis samme udgivelse.
4. Når siden siger **Makerværkstedet er klar**, vælg **Åbn Makerværkstedet**
   eller brug skrivebordsgenvejen. Log ind med skolens lærerkode. Kontrollér,
   at skolen og lasermodellen er de forventede, og at serverens kø kan hentes.
5. En rigtig prøve med Beam Studio, materialer og fysisk laserstart foretages
   særskilt med læreren. En tom, tilgængelig kø beviser ikke fysisk forbindelse
   til laseren. Se [de to opsætninger](TO-OPSAETNINGER.md) for maskinprogrammer
   og stationens brug.

Brug skrivebordsgenvejen, når stationen skal åbnes. Hvis Windows har optaget
dens tidligere lokale adresse, vælger en separat station en ny ledig adresse
ved start. Server, lærerkode og gemte plader bevares. En gammel browserbogmærkeadresse
kan derfor holde op med at virke; genvejen bruger den aktuelle adresse.

## Før Windows-serveren opdateres

På en opsætning med alt på én computer: vælg **Stop laserstation** hos den
daglige Windows-bruger, før serverens EXE køres. Browserfanen alene stopper
ikke stationen. Guiden afviser opdatering, hvis stationen eller en anden
ukontrolleret programproces stadig kører; køer og server er da endnu ikke stoppet.
En ældre station uden den nye stopfunktion skal afsluttes ved Windows-udlogning;
lad skolens IT gennemføre den første opdatering fra en separat administratorsession.

Hvis en tidligere skoleopsætning ikke blev færdig, skal den færdiggøres med
`Setup-School.ps1 -Resume` og de oprindelige skolevalg før en opdatering.
En halvfærdig skoleopsætning bliver afvist og meldes ikke som opdateret.

## 2. Installér Debian-eleven

1. Brug den skrevne installations-USB på fysisk udstyr. I Hyper-V tilknyttes
   samme ISO til en NY Gen-2 VM med Secure Boot og én tom disk.
2. Start fra mediet. Vælg **Dansk** ved tastaturspørgsmålet, hvis det vises.
   Netværket skal have internet under Debian-installationen.
3. Har computeren flere interne diske, skal du først vælge systemdisken
   ud fra navn, størrelse og model. **Annuller** er valgt fra start;
   USB-diske og installationsmediet kan ikke vælges. Ved én intern disk
   vælges den automatisk. Kontrollér derefter den viste disks model og
   størrelse. Sletbekræftelsen står på **Nej**; vælg kun **Ja**, når den
   aftalte disk er vist, og alle dens data må slettes. De øvrige diske
   bruges ikke til installationen. Afbryd, hvis du er i tvivl.
4. Vent, til installeren selv slukker. Fjern USB eller frakobl ISO'en og
   start fra den installerede disk.
5. Første boot tilbyder **Skoleopsætning: tryk S og Enter inden 10 sekunder**.
   Til offlineprøven trykker du Enter eller venter. Den lokale elevflade
   åbner. Skoleopsætningen tilbydes igen ved genstart, så længe eleven ikke
   er tilmeldt.
6. **Samlet `.2`-ISO:** Åbn Laser, tegn en figur, gem, gå til start og
   genåbn fra projekthylden. Prøv Folie og 3D, hvor grafikken understøttes.
   **Modulær basis:** Kontrollér start og projekthylde uden valgfrie
   appfliser; fortsæt til tilmelding. VM'ens grafik beviser ikke fysisk
   GPU-/WebGL-understøttelse.

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
6. Kontrollér eleven i lærerens panel. I den modulære udgave får en ny
   enhed skolens standardapps, når updateren har kontakt. Åbn **Appkatalog**
   og følg den rapporterede status; en ordre eller download er ikke en
   færdig installation. Vælg eventuelt app og computer og bestil
   **Installér på manglende**. Ingen lokal appflise før installation er
   forventet på en tom basis.
7. Afprøv efter aftale elevskift, gem/genåbn, offline genstart og lærerens
   adgangslukning i de faktisk installerede apps. Prøv kun aflevering i
   det aftalte testmiljø. **202: gemt i udbakken** betyder afventende
   forbindelse; modtagelse i serverens kø er ikke fysisk færdig produktion.

Ved geninstallation afmeldes det gamle computernavn først i serverens
administration. Genbrug ikke en anden skoles profil eller certifikater.

## Daglig administration i den modulære udgave

**Appkatalog** skelner mellem **Installerede apps** og **Kan installeres**.
Vælg computerne og brug installering, opdatering eller afinstallation.
Gemte projekter og ventende afleveringer bevares ved fjernelse. **Apps til
nye elevcomputere** ændrer kun standardsættet for nye enheder.

**Apps og internet** styrer lærerens adgang til undervisningen. En
lukning fjerner ingen software. Ved serverudfald er installerede lokale
apps åbne; eksterne tjenester kræver stadig internet.

I **Skoleprofil** vælges navn, PNG-logo, farve og en tilgængelig
skrifttype. Kontrollér forhåndsvisningen og vælg **Gem skoleprofil**.
Profilen distribueres til tilmeldte elever og bevares til offline brug.
Se [skoletilpasning](SKOLETILPASNING.md) for grænser og eksisterende
private profiler.

## Opdatering og fejl

Viser EXE'en **Installationen blev ikke færdig**, er programmet ikke meldt
klar. Vælg **Vis installationslog** for at åbne den konkrete log, eller kopiér
filstien. Få den IT-ansvarlige til at kontrollere årsagen og eventuel delvis
installation før et nyt forsøg. Den nye guide bevarer den native fejl i
loggen og afslutter med fejlkode `20`; en færdig udpakning er ikke en
vellykket installation. Ældre kandidater skal vurderes efter leverancens
testkvittering, også selv om de viser en generisk færdigside.

Hvis en overgang til tilvalgsapps bliver ved med at fejle, kan læreren i
**Teknik → Opdateringer** læse computerens fejlkode og vælge **Stop genforsøg**.
Det stopper nye forsøg på serveren og bevarer historik og elevprojekter.
En igangværende installation får lov at afslutte. Vent på computerens nye
status og ret årsagen, før overgangen bestilles igen. Se de konkrete
fejlkoder i [app-platformens migrationsvejledning](APP-PLATFORM.md).

Samme rolle-EXE viser **Opdatering fundet** ved en kendt installation og
bevarer profil, identitet og data. Serverens køer bliver pauset, indtil
læreren fortsætter. Genoptagelse, backupgendannelse og opdatering er
forskellige handlinger. En eksisterende samlet Linux-installation skal
bruge den ordnede bro-/appmigration, ikke geninstalleres med basis-ISO
eller nulstille elevprojekter igen. Se
[appplatformen](APP-PLATFORM.md), [opdateringer](GITHUB-OPDATERINGER.md)
og [Kom godt i gang](KOM-GODT-IGANG.md).

Hvis et trin fejler, stop dér og notér trinnet og den synlige fejl. Bevar
data og logs. Læs leverancens aktuelle status før et nyt forsøg; en kilde-
eller VM-prøve er ikke en godkendelse af skolens drift eller fysisk hardware.
