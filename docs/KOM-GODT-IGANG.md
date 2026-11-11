# Kom godt i gang med Makerværkstedet

> Offentlig dokumentation: LOKAL_IP er en pladsholder. Brug skolens egne adresser.

**Kreditering: Klostermarksskolen.** Denne vejledning er til læreren og den
person, der hjælper med skolens Windows-server. Den kræver ingen AI og beskriver
den normale installation fra en færdig releasepakke. Kildekoden er kun relevant,
hvis skolen selv vil bygge en pakke; se [kildeudgivelsen](PUBLIC-RELEASE.md).

## Det skal I have klar

- En Windows 10/11 x64-PC eller server på makerspacets eget LAN. Giv den en
  fast IPv4-adresse eller en DHCP-reservation. Elevcomputere og server skal
  kunne nå hinanden på samme relevante net.
- Linux-elevcomputere med Intel/AMD (amd64) og en USB-nøgle til ISO'en. ISO'en
  kan muligvis starte på enkelte x86_64-Chromebooks, men model, firmware,
  administreret status og USB-boot skal prøves særskilt. ARM-Chromebooks kan
  ikke bruge ISO'en, og denne vejledning lover ikke Chromebook-support.
- En sikker USB- eller netværksmappe til den offentlige skoleforbindelse og en
  beskyttet backupplacering. Forbindelsesfilen må gerne deles med elevmaskiner;
  private nøgler og serverdata må ikke kopieres til dem.

Makerværkstedet er lærerens hovedsystem. Det virker også uden en eneste
tilmeldt elevcomputer. Windows-elevklienten er udgået og skal ikke installeres.

Brug en komplet, versionsmærket releasepakke fra den valgte
[udgivelseskilde](GITHUB-OPDATERINGER.md). Kildearkivet er byggegrundlag og
indeholder ikke i sig selv en færdig Windows-pakke eller Linux-ISO.
Kontrollér pakkens indhold og den medfølgende installationsvejledning.
[Én eller to computere](TO-OPSAETNINGER.md) beskriver den grafiske guide;
ældre pakker kan have en anden indgang. En VM er valgfri.

## Vælg først: ren installation eller opdatering

De fire produktveje skal være adskilte. En opdatering skal bevare den
eksisterende skoles data og opsætning; den må ikke kræve en ny tom installation.

| Computer og handling | Formål og databevaring | Aktuel indgang og status |
| --- | --- | --- |
| **Linux-elev: ren installation** | Installér Debian og værksteder på den valgte disk, og tilmeld computeren. Diskens gamle indhold kan slettes. | Installations-ISO og første tilmelding; se trin 3. |
| **Linux-elev: opdatering** | Opdatér programmer på en allerede tilmeldt computer med bevarede projekter, identitet og skoleforbindelse. | Eksisterende SSH-/Ansible-administration; se afsnittet om opdatering nedenfor. En samlet releaseopdatering er ikke færdigverificeret. |
| **Windows-server: ren installation** | Opret et nyt værkstedssystem med skolens egne nøgler, netværk og opsætning. | Windows-pakkens `START.cmd`; se trin 1. |
| **Windows-server: opdatering** | Bevar nøgler, tilmeldinger, maskiner, køer, workflows og historik. | GitHub-controllerens `school-flat-v1`-adapter eller lokal `Invoke-SchoolUpdate.ps1`. `Maintain-School.cmd` kræver særskilt `generation-v1`; se [serveropdatering](MAKERSPACE-OPDATERING.md). |

En **VM følger sin rolle og sit operativsystem**: Linux-elev-VM'en bruger
elevvejene, og en Windows-server i en VM bruger servervejene. Valget `vm` i
Windows-guiden er en serverprofil. Lærerens browser bruger serveren og kræver
ingen særskilt elevinstallation på lærerens Windows-computer.

**Gendannelse fra backup** er en særskilt redningsvej. **Genoptag installation**
fortsætter et afbrudt installationsforsøg. Ingen af delene er en almindelig
versionsopdatering, og en ny ISO skal ikke bruges til at opdatere en elevcomputer.

## 1. Installér Windows-serveren

1. Udpak hele Windows-serverpakken i en ny mappe på serveren. Brug ikke en
   eksisterende installation som mål for førstegangsopsætningen.
2. En IT-medarbejder kan først køre den skrivefri kontrol. Det er frivilligt;
   læreren kan gå direkte til `START.cmd`:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\school-setup\Setup-School.ps1 -CheckOnly
   ```

3. Højreklik på `START.cmd`, vælg **Kør som administrator**, og godkend Windows'
   normale administratorprompt.
4. Guiden spørger efter fem valg: skolens navn; rollen `vm` (Windows-
   server/kø/lærerpanel) eller `makerspace` (samme plus lokal laserstation);
   serverens faste IPv4-adresse; elevnettet i CIDR-format (fx
   `LOKAL_IP/24`); og farvetema. Ved `makerspace` vælger du også den
   faktiske lasermodel, Beambox II eller HEXA.
5. Accepter kun opsummeringen, når adresse og elevnet er korrekte. Opsætningen
   opretter TLS, beskyttet serverdata, planlagte opgaver, firewallregler og
   genvejen **Makerværkstedet.lnk** på det offentlige skrivebord. Den private
   nøgle bliver på serveren.

Standardportene er print `8765`, laser/design `8766`, lærer-backend `8767`,
lærerens HTTPS-panel `8780`, elevgateway `8770–8773`, elevtilmelding `8774` og
laserstation `8790`. Brug kun andre porte gennem opsætningens eksplicitte
felter, og hold dem forskellige. Åbn kun de nødvendige porte på skolens LAN;
lav ikke portviderestilling fra internettet.

Hvis serveradressen ændres, skal der oprettes ny TLS-/forbindelsesopsætning og
elevcomputerne tilmeldes igen. Brug ikke gamle certifikater eller enrollment-
filer på en ny server.

## 2. Åbn lærerens panel og klargør tilmelding

Åbn genvejen **Makerværkstedet.lnk**, eller vælg punkt 8 i `Manage-School.cmd`.
Første gang vælger læreren selv en lærerkode på mindst otte tegn i browseren;
ved senere besøg bruges denne kode. Herfra kan læreren se elevcomputere, printere,
maskiner, køer og versionsstatus. Der må gerne stå nul elevcomputere.

`Manage-School.cmd`, punkt 5, viser de særskilte lærer-/printkoder til
Printværkstedet og Laserværkstedet. De er ikke det samme som koden til det
samlede kontrolpanel.

Åbn `Manage-School.cmd` som administrator ved behov. De vigtigste punkter er:

1. udsted en engangskode og giv hver computer et unikt navn;
2. kontrollér server- og firewallopsætning;
3. kopier den offentlige `school-connection.json` til en ny, tom USB-mappe;
4. afmeld en gammel elevcomputer før en geninstallation;
5. opret og kontrollér backup eller gendan til en isoleret prøvemappe.

Engangskoden gælder i 30 minutter. Del kun den aktuelle offentlige
`school-connection.json`; den indeholder ikke serverens private nøgle.

## 3. Installér en Linux-elevcomputer

1. Skriv den medfølgende Debian amd64-ISO som diskbillede til en USB-nøgle.
   Kopiering af ISO-filen til USB'en er ikke nok.
2. Tag backup af elevcomputerens filer. Kontrollér diskmodel og størrelse i
   installationsprogrammet. ISO'en sletter den valgte interne disk efter en
   ekstra bekræftelse og stopper, hvis diskvalget er uklart.
3. Sørg for internet under Debian-installationen, så netinst-systempakker kan
   hentes. Værksteder og CaDoodle følger med ISO'en.
4. Start den installerede Linux-computer, indsæt USB'en med
   `school-connection.json`, og vælg skolefilen. Indtast computerens unikke
   navn og engangskoden fra lærerens panel.
5. Vent på færdig tilmelding, genstart og kontrollér, at elevens værkstedskort
   åbner. Gentag med et nyt navn og en ny kode for næste computer.

Ved netudfald skal værksteder, CaDoodle og Internet fortsat være åbne, også ved
kold opstart. Når serveren kommer tilbage, gælder lærerens aktuelle valg igen.
Elevprojekter er lokale på elevcomputeren; afleveringer, køer og lærerens valg
ligger på serveren.

## Opdatér et eksisterende system

### Linux-elevcomputer eller Linux-elev-VM

Den eksisterende [administrationsvej](../linux-elevhub/administration/README.md)
åbnes med `linux-elevhub/administration/Open-Elevhub-Administration.cmd` fra
administrationskilderne. Den bruger SSH og Ansible, vælger computerliste og
tilbyder **Tjek ændringer** efterfulgt af **Anvend opdatering**. Der kan vælges
**CaDoodle** eller **Hele elevsystemet**, uden at Debian geninstalleres.

Dette er en teknisk administrationsvej, ikke en færdig versionslåst
releaseopdatering. Hele systemets opskrift skriver også skolekonfiguration,
enhedsoplysninger og SSH-adgang fra administrationscomputerens provisionering.
Brug derfor den eksisterende skoles kontrollerede opsætning; en ny generisk
skoleprofil er ikke en opdatering af den tilmeldte computer. Elevprojekterne
ligger uden for softwarekopieringen. Åben CaDoodle springes over, og en aktiv
elevskærm genstartes som udgangspunkt ikke; baggrundstjenester kan genstarte.

En færdig releaseopdatering skal vise kilde- og målversion, kontrollere den
rette computer, bevare projekter/identitet/skoleopsætning og rapportere
udeladte komponenter. Den skal prøves på én computer, inklusive afbrydelse
og genoptagelse, før flere computere opdateres. En udeladt komponent må ikke
give en misvisende melding om, at hele computeren er opdateret.

### Windows-server på fysisk computer eller i VM

Brug ikke `START.cmd`, geninstallation eller `Setup-School.ps1 -Resume` som
versionsopdatering. `-Resume` fortsætter kun det samme afbrudte
installationsforsøg med den oprindelige pakke og opsætning.

Den flade serverinstallation (`school-flat-v1`) opdateres gennem lærerens
[GitHub-opdateringskø](GITHUB-OPDATERINGER.md) eller IT-indgangen
`Invoke-SchoolUpdate.ps1`. Den lokale adapter kræver de understøttede faste
stier, opgaver og uændret runtime; se
[serveropdatering og fejlgrænser](MAKERSPACE-OPDATERING.md).
`Maintain-School.cmd` er kun til det særskilte `generation-v1`-layout.

Kontrollér backup, kilde-/målversion, databevaring og installerede filhashes.
Efter opdateringen holdes printkøen pauset til lærerens kontrol. En hentet
pakke eller bestået kildeprøve er ikke installationsbevis. Geninstallation
med manuel filkopiering er ikke en opdatering.
## 4. Tilføj maskiner og workflows sikkert

Læreren kan oprette understøttede maskiner og versionerede workflows i
`/makerspace`-panelet uden at redigere kode. En registreret maskine har stadig
brug for den rigtige eksterne driver eller producentsoftware. En maskine med
model, IP-adresse og serienummer bliver ikke automatisk til understøttet
hardware.

### 3D-printer

Tilføj kun en virkelig printer på makerspacets private LAN med model, synligt
navn, privat IPv4-adresse, serienummer, LAN-adgangskode og pladetype. Nye
printere er deaktiverede, indtil læreren har kontrolleret identitet og
forbindelse. Læreren kontrollerer og frigiver pladen manuelt. Kameravisning
giver ingen automatisk vurdering eller temperaturgaranti.

Til hardwarefri køprøver bruges en særskilt, tydeligt mærket DEMO-opstilling
med simulerede maskiner og egne testdata. Den almindelige skoleinstallation
opretter ikke simulatorer. Flyt aldrig demoens data ind i skolens installation.

IT kan oprette en isoleret prøve med
`runtime\python.exe -B -m app.demo_workshop setup --data-root <ny-tom-demomappe>`.
Kommandoen opretter simulerede maskiner, workflows og kødata. TLS og netværk
skal klargøres separat. En simulation dokumenterer ingen fysisk printer.
### Laser og manuel station

En laserstation kræver den faktiske maskine, dens producentdriver og eventuelt
ekstern software som Beam Studio. En manuel station kræver også, at det valgte
workflow og værktøj findes lokalt. Registrering eller `produce` giver kun
lærerens tilladelse til næste trin; det starter ikke fysisk laserudstyr.

Til en isoleret køtest bruges en eksplicit mærket **DEMO**-laser. Kontroller,
at stationens navn siger **DEMO · Laser – ingen maskine**. Klargøring,
godkendelse, simuleret overdragelse, afslutning og afhentning bruger de normale
køtrin. I skoleopsætningen prøves faktisk laserudstyr særskilt; dens `produce`
er fortsat en tilladelse til overdragelse, ikke en automatisk fysisk start.

## 5. Backup og gendannelse

Gem arbejde og stop server, tilmelding og lokal laserstation før backup. Brug
`Manage-School.cmd`, punkt 9, til en ny backupmappe; punkt 10 kontrollerer senere
manifest og kontrolsummer. Backupen kan indeholde køer, printeropsætning,
workflows, lærerlogin, elevdata, certifikater og private nøgler. Opbevar den på
et beskyttet, separat medie; en kopi på samme disk beskytter ikke mod diskhavari.

Punkt 11 gendanner til en ny isoleret prøvemappe og ændrer ikke den aktive
installation. Brug den kun til kontrol. Aktivering af en gendannet server
er et særskilt forløb. Den nuværende `Maintain-School.cmd` og gendannelse på
en erstatningsvært kræver `generation-v1`; de er ikke en færdig gendannelsesvej
for det flade `school-flat-v1`-layout. Se [backup og afgrænsninger](MAKERSPACE-BACKUP.md).
Ændr aldrig aktive kø- eller printerfiler manuelt for at omgå den manglende vej.

## 6. Når noget ikke virker

- **Panel eller elevcomputer kan ikke nå serveren:** kontrollér fast IP,
  subnet, kabel/Wi-Fi, Windows-firewall og at begge maskiner er på samme LAN.
  Test ikke via internet eller portviderestilling.
- **TLS-/tilmeldingsfejl:** eksportér en frisk `school-connection.json`, brug
  en ny engangskode, og kontrollér at serverens adresse ikke er ændret.
- **Værksted mangler ved offline opstart:** lad computeren starte færdigt og
  kontrollér den lokale Linux-installation; offline skal ikke lukke lokale
  værksteder.
- **Printer står offline:** kontrollér strøm, LAN-adresse, model, serienummer,
  LAN-kode og den eksterne producentforbindelse. Slet ikke printeropsætningen
  for at få køen til at se tom ud.
- **Laserjob starter ikke:** det er forventet, hvis driver/ekstern software,
  fysisk maskine eller lærerens godkendelsestrin mangler. `produce` er ikke en
  hardwaregaranti.
- **Køtest påvirker en rigtig maskine:** stop straks, skift til den isolerede,
  tydeligt mærkede demo, og kontrollér at demoens status siger simulation.

## Godkendelsesprøve før eleverne tager systemet i brug

Gå gennem denne korte prøve med én lærercomputer og én Linux-elevcomputer:

1. lærer logger ind og ser kontrolpanelet med nul eller den forventede mængde
   elevcomputere;
2. elevcomputer tilmeldes med frisk kode og vises online;
3. ét lokalt værksted åbnes og lukkes fra panelet;
4. elevcomputeren genstartes uden serverforbindelse, og lokale værksteder,
   CaDoodle og Internet åbner;
5. forbindelsen gendannes, og lærerens aktuelle valg anvendes;
6. en særskilt DEMO-opstilling gennemfører print- og laserkøforløb
   uden fysisk udstyr; skolens almindelige installation prøves med skolens
   godkendte udstyr;
7. backup oprettes og kontrolleres, og en isoleret gendannelsesprøve består;
8. skolens faktiske printer/laser prøves separat af en ansvarlig voksen, før
   elevernes kø får adgang.

Gem dato, releaseversion, serveradresse, testnavn og resultat. En bestået
softwarekontrol er ikke det samme som en bestået prøve af skolens fysiske
printer, laser, USB-boot eller Chromebook-model.
