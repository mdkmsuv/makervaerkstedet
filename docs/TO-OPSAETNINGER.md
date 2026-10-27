# Makerværkstedet på én eller to computere

> Offentlig dokumentation: LOKAL_IP er en pladsholder. Brug skolens egne adresser.

**Kreditering: Klostermarksskolen.** Vejledningen kræver ingen AI.

Vælg den opsætning, der passer til skolens udstyr. Begge har ét hovedsystem
med lærerpanel, maskiner, workflows og køer. Linux-elevcomputere kan tilføjes
bagefter; systemet kan også bruges uden elevcomputere.

| Valg i installationsguiden | Hvor ligger hovedsystemet? | Hvor betjenes laseren? |
| --- | --- | --- |
| **Alt på én Windows-computer** | På computeren ved værkstedet | På samme computer |
| **Central server/VM + separat lasercomputer** | På skolens Windows-server, fysisk eller i en VM | På en separat Windows-computer med forbindelse til den centrale kø |

En VM er valgfri. Valget handler om fordeling af opgaver, ikke om færre
funktioner i løsningen på én computer. Windows-laserstationen er et
lærerværktøj; elevværkstederne kører fortsat kun på Linux.

Vejledningen beskriver den grafiske guide i kildekoden. Brug en komplet,
kontrolleret release, der indeholder guiden og de relevante stationværktøjer.
Kildekoden og en lokal programprøve dokumenterer ikke en færdig pakke eller
en samlet installation på skolens udstyr. Følg altid den valgte pakkes
medfølgende vejledning; ældre pakker kan have andre menuvalg.
## Før installation

1. Hav en komplet, udpakket Windows-skolepakke med den nye grafiske guide klar.
   Brug samme udgivelse på server og separat lasercomputer.
2. Giv hovedsystemet en fast IPv4-adresse eller DHCP-reservation. Server,
   lasercomputer og eventuelle elevcomputere skal være på det værkstedsnet,
   som angives i guiden, fx `LOKAL_IP/24`. Guiden åbner kun for dette net;
   den opsætter ikke adgang på tværs af andre VLAN eller fra internettet.
3. Hav skolens navn, netadresse og lasermodel klar. Den medfølgende
   laserstation er til Beambox II eller HEXA. Andre maskiner kan registreres
   i kontrolpanelet, men får ikke automatisk en fysisk driver.
4. Installér Beam Studio på computeren ved laseren, hvis I bruger dette
   FLUX-forløb. Bevar normale krav til materialer, kamera og betjening.

## Guiden hjælper dig gennem fem trin

`START.cmd` åbner et Windows-vindue: **Opsætning → Skolen → Netværk →
Gennemgå → Installation**. Du kan gå tilbage og rette oplysningerne.
Der installeres først noget, når du vælger **Start installation** på
gennemgangssiden. Først dér beder serverdelen om Windows-godkendelse.
På en separat lasercomputer erstattes skoleoplysninger og netværk af
serverens forbindelsesfil.

| Oplysning | Sådan finder du den |
| --- | --- |
| Skolens navn | Skriv det navn, lærere og elever skal se. Klostermarksskolens kreditering bevares separat. |
| Laserens model | Se mærkaten på laseren eller modelnavnet i Beam Studio. Vælg kun en af de viste understøttede modeller. |
| Udseende | Vælg rød, gul eller grøn. Det kræver ingen tekniske oplysninger. |
| Serverens IPv4-adresse | Vælg netværkskortet til værkstedsnettet i guidens liste. Adressen udfyldes som forslag. Brug adressen på serveren eller inde i serverens VM, ikke adressen på VM-værten eller routeren. |
| Værkstedsnettets område | Udfyldes som forslag sammen med adressen, fx `LOKAL_IP/24`. Skolens IT skal bekræfte området og fast adresse eller reservation. Kopiér ikke vejledningens eksempel som skolens adresse. |
| Lasercomputerens forbindelsesfil | Hentes på den centrale server i **Offentlige dokumenter → Elevvaerkstedet**. Vælg filen med knappen **Vælg fil…**; guiden viser derefter skole, server og model til kontrol. |

Hvis flere netværkskort vises, vælger guiden ikke et for dig. VPN,
Wi-Fi og virtuelle netværkskort kan pege på forskellige net. Knappen
**Kopiér tjekliste til IT** samler de indtastede oplysninger og spørgsmål,
så du kan indsætte dem i en mail eller besked. Ingen besked sendes automatisk.
Guiden ændrer ikke computerens IP-adresse eller opretter en reservation.
Når IT har bekræftet oplysningerne, markeres bekræftelsen under felterne.
Ændrer du adresse eller netværksområde, skal de bekræftes igen.

På små skærme kan indholdet rulles; knapperne nederst bliver stående.
Felterne kan også betjenes med Tab, mellemrum og Enter. Der skal ikke
indtastes adgangskoder eller private nøgler i installationsguiden.

Hjælpen er også indbygget i vinduet. **Hvad skal jeg vælge?** forklarer
opsætningen, og **Hvor finder jeg oplysningerne?** forklarer netværkskort,
VM-adresse og fast adresse. Under installationen skifter hjælpen med fasen.
Efter afslutning vises konkrete trin til lærerpanel, forbindelsesfiler og
prøveopgave. Ved fejl kan **Kopiér hjælp til IT** samle status og de relevante
kontrolresultater; foldudfeltet med tekniske oplysninger indeholder ved en
afbrydelse også hjælp til genoptagelse med de oprindelige valg. Kopiering
starter ingen kommando og sender ingen besked.

## A. Alt på én Windows-computer

1. Dobbeltklik på `START.cmd` i den udpakkede pakke.
2. Vælg **Alt på én Windows-computer** og **Næste**.
3. Udfyld skole, lasermodel og udseende. Vælg netværkskort, kontrollér de
   foreslåede netværksoplysninger med IT, og markér bekræftelsen.
   Gennemgå oplysningerne, vælg **Start installation**, og godkend Windows'
   administratorprompt. Vent på resultatet af installation og efterkontrol.
4. Åbn skrivebordsgenvejen **Makerværkstedet**. Vælg en lærerkode på mindst
   otte tegn første gang. Dette er skolens hovedsystem.
5. Åbn genvejen **Elevhub Laserstation**. Den bruger køen på samme computer.
   `Manage-School.cmd`, punkt 5, viser den særskilte kode til Laserværkstedet.
6. Følg [Kom godt i gang](KOM-GODT-IGANG.md) for maskiner, workflows,
   elevtilmelding og en kontrolleret prøveaflevering.

Computeren skal være tændt, når andre enheder skal bruge systemet.

## B. Central server eller VM og separat lasercomputer

### Først på Windows-serveren

1. Dobbeltklik på `START.cmd`. Vælg **Server og separat lasercomputer**,
   derefter **Den centrale Windows-server eller VM** og **Næste**.
2. Udfyld skole, lasermodel, udseende og IT-bekræftede netværksoplysninger.
   Gennemgå oplysningerne, vælg **Start installation**, og godkend Windows'
   administratorprompt. Vent på resultatet af installation og efterkontrol.
3. Åbn **Makerværkstedet** på serveren og vælg kontrolpanelets lærerkode.
   Førstegangsoprettelsen af koden skal ske på selve serveren.
4. Find de offentlige forbindelsesfiler i
   `C:\Users\Public\Documents\Elevvaerkstedet`. Guiden viser filstierne;
   ved gentagelser kan filnavnet have et tidspunkt til sidst.
5. Kopiér den nye **laser-station-connection.json** til en USB-nøgle eller
   anden fælles mappe til lasercomputeren. Den indeholder serveradresse,
   skolens offentlige certifikat, stationsidentitet og model.
   **school-connection.json** er den anden fil og bruges til Linux-elever.
   Ingen af disse filer indeholder serverens private nøgler.

### Derefter på Windows-computeren ved laseren

1. Log ind som den Windows-bruger, der skal bruge laserstationen. Udpak
   samme skolepakke og dobbeltklik på `START.cmd` som almindelig bruger.
2. Vælg **Server og separat lasercomputer**, derefter **Lasercomputeren —
   serveren er allerede installeret**. Brug **Vælg fil…** til den nye
   `laser-station-connection.json`. Kontrollér skole, server og lasermodel
   på gennemgangssiden, og vælg **Start installation**.
3. Stationen installeres for denne Windows-bruger. Skolens offentlige
   certifikat tilføjes denne brugers certifikatlager. Genvejene
   **Laserstation**, **Stop laserstation** og **Makerværkstedet** oprettes.
   Der oprettes ingen ekstra skole,
   køserver, Windows-opgave eller firewallregel på lasercomputeren.
4. Åbn **Makerværkstedet** for serverens fælles lærerpanel. Log ind med den
   kode, du oprettede på serveren.
5. Åbn **Laserstation**. Brug Laserværkstedets særskilte kode fra serverens
   `Manage-School.cmd`, punkt 5. Stationen henter opgaver i serverens laserkø.
6. Kontrollér med en prøveaflevering, at samme job vises centralt og på
   stationen. Kontrollér tilknytningen til den rette station og workflow i
   lærerens maskinadministration. En programtest er ikke et fysisk lasersnit.

Kø og skoledata ligger centralt. Lokale laserplader ligger normalt i
`%USERPROFILE%\Documents\Laserplader` og skal sikkerhedskopieres. Det er denne
konkrete sti, også hvis Windows' Dokumenter-mappe er omdirigeret til OneDrive.
Serveren skal være tændt og kunne nås, når stationen arbejder i køen.
Forbindelsen går gennem serverens HTTPS-port, normalt `8780`; den interne
lærer-backend på `8767` forbliver lokal på serveren.

## Slutresultat og hjælp ved fejl

Guiden viser installationsloggen og kontrollerer resultatet før en
succesbesked. På serveren kontrolleres bl.a. installationens tilhørsforhold,
skoleprofil, kørende server- og tilmeldingsopgave, netværksporte og HTTPS-
lærerpanel. På den separate station kontrolleres installeret version,
skoleforbindelse, offentligt certifikat og programmets informationskommando.
Dette er en lokal kontrol; skolens netværk, elevmaskiner og fysiske maskiner
skal stadig afprøves som beskrevet ovenfor.

Ved fejl: vælg **Kopiér log**, og bevar logmappen, som vises i feltet.
Hvert forsøg får sin egen mappe under
`%LOCALAPPDATA%\Makervaerkstedet\SetupLogs`. Her ligger status,
installationslog og eventuelle efterkontrolresultater. Start ikke en ren
installation oven i et afbrudt forsøg; brug genoptagelse nedenfor.
Hvis Windows-godkendelsen blev afvist, før noget startede, kan guiden lukkes
og åbnes igen. Under selve installationen holder guiden sit vindue åbent.

IT kan fortsat bruge tekstmenuen med `START.cmd -TextMode`. Det vælger
den eksisterende kommandovej og er ikke den grafiske guides efterkontrol.

### Beam Studio og andre installationsstier

Beam Studio og FLUX-programmet følger deres egne installationsvilkår og skal
være installeret på lasercomputeren. Standardstierne er
`C:\Program Files\Beam Studio\Beam Studio.exe` og undermappen
`resources\backend\flux_api\flux_api.exe`. Køen kan prøves uden disse
programmer; fysisk klargøring og overførsel kræver den relevante opsætning.

Hvis de ligger et andet sted, kan skolens IT bruge stationsinstalleren
direkte med de konkrete eksisterende filer. Eksempel fra pakkens rod:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\school-setup\Setup-LaserStation.ps1 -Action Install -ConnectionFile "E:\laser-station-connection.json" -BeamStudioPath "D:\Beam Studio\Beam Studio.exe" -FluxApiPath "D:\Beam Studio\resources\backend\flux_api\flux_api.exe"
```

Tilpas stierne til computeren. Det er en alternativ ren installation af den
separate station, ikke et ekstra trin efter at stationen allerede er installeret.

## Opdatering er en anden handling end installation

| Del | Ren installation | Opdatering |
| --- | --- | --- |
| Windows-hovedsystem, med eller uden VM | `START.cmd` og valg A eller B/server | Brug kun en opdateringsvej, som understøtter installationens format. Det flade `school-flat-v1`-layout bruger GitHub-controllerens serveradapter eller `Invoke-SchoolUpdate.ps1`; se [status](MAKERSPACE-OPDATERING.md). |
| Separat Windows-laserstation fra den nye guide | `START.cmd` og B/lasercomputer, eller `Install-LaserStation.cmd` | Afslut arbejdet, brug **Stop laserstation**, og kør `Update-LaserStation.cmd` fra den nye komplette pakke som samme Windows-bruger. At lukke browserfanen er ikke nok. |
| Linux-elevcomputer eller Linux-elev-VM | ISO og tilmelding | [SSH-/Ansible-administration](../linux-elevhub/administration/README.md); ISO'en er ikke en opdatering. |

En stationsopdatering bevarer skoleforbindelse, certifikat, identitet,
lasermodel, plader og driverindstillinger. Den vælger først det nye program,
når hele kopien er kontrolleret. Den afviser en kørende station og en ukendt
historisk installation. Brug ikke stationens opdatering til serveren eller
den integrerede station i løsning A.

## Opdateringskø i lærerens kontrolpanel

Den aktuelle kildekode har nu **Opdatér computere** i lærerens kontrolpanel.
Læreren vælger en gennemgået udgivelse og computere, ser ønsket version og
status og kan sætte køen på pause, genoptage og bestille genforsøg. Linux-elever
og Windows-lasercomputeren har hver sin rolle. En slukket computer bliver i
køen, og en klargjort Linux-opdatering vises som afventende genstart, indtil
resultatet er bekræftet efter normal opstart.

SSH-forbindelsen starter altid fra hovedsystemet: fra Windows-serveren eller
inde i dens VM ved central drift, eller fra den fælles Windows-computer ved
alt på én computer. Private nøgler bliver på hovedserveren. Læreren indtaster
hverken scripts, SSH-adresser, pakkeuploads eller filstier i panelet. Skolens IT
klargør katalog, identiteter, fastholdte værtsnøgler og serveropgaver.
Se [lærerforløb, serveropsætning og worker-kontrakt](OPDATERINGSKOE.md).

På en ældre elevinstallation, som først kommer online ved opstart, kan første
opstart bruges til serverkontakt og klargøring. Først den efterfølgende
normale genstart anvender den klargjorte udgivelse. Lad eleverne gemme arbejdet;
serveren foretager ingen tvungen genstart.

Køstatus er ikke installationsbevis. Kontrollér den bekræftede version efter
normal opstart og foretag en afgrænset prøve på én computer før bred udrulning.

## Hvis installationen bliver afbrudt

- **Server:** `Setup-School.ps1 -Resume` genoptager samme forsøg med samme
  pakke og skolevalg. I løsning B kræves fortsat `-Role vm
  -RemoteLaserStation` og den oprindelige lasermodel. Det er ikke en opdatering.
- **Separat station før færdig installation:** Kør følgende fra den
  oprindelige pakkes rod som samme bruger. Den eksisterende journal bestemmer
  forbindelse og valg; en anden udgivelse afvises:

  ```powershell
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\school-setup\Setup-LaserStation.ps1 -Action ResumeInstall
  ```

- **Programmet er installeret, men certifikat eller genveje fejlede:** Fejlen
  fortæller dette udtrykkeligt. Færdiggør brugeropsætningen uden geninstallation:

  ```powershell
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\school-setup\Setup-LaserStation.ps1 -Action Update -FinishSetup
  ```

- **Afbrudt stationsopdatering:** Den tidligere valgte version bevares, indtil
  en ny er fuldt kontrolleret. Kør opdateringen igen. En ufærdig programkopi
  kan ligge tilbage; den bruges ikke automatisk. Slet ikke plademappen.

Hvis en anden installationssti blev valgt, tilføjes samme `-InstallPath`
til genoptagelse eller færdiggørelse. Gendannelse fra backup er fortsat en
særskilt handling; se [backupvejledningen](MAKERSPACE-BACKUP.md).
