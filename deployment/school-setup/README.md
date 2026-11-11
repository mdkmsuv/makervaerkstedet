# Windows skoleopsætning

Denne mappe indeholder den danske opsætning af Makerværkstedet på en
Windows x64-styreenhed samt en valgfri separat laserstation. Den er skrevet til skolens eget LAN; skolens navn,
faste IPv4-adresse, elevsubnet og lokale nøgler vælges under opsætningen.

## Før start

- Brug en fysisk Windows x64-PC eller Windows-VM med fast adresse eller
  DHCP-reservation.
- Udpak hele pakken til førstegangsopsætning; en eksisterende installation
  overskrives ikke automatisk. Den installerede vedligeholdelseswrapper
  gælder kun `generation-v1`. Den flade installation bruger GitHub-controllerens serveradapter eller `Invoke-SchoolUpdate.ps1`; se
  [de fire særskilte produktveje](../../docs/KOM-GODT-IGANG.md#vælg-først-ren-installation-eller-opdatering).
- Kør fra den udpakkede pakkes rod:
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\school-setup\Setup-School.ps1 -CheckOnly`
  for en skrivefri kontrol. Kontrollen genstarter ikke Windows eller åbner
  paneler; releasekontrollen skal godkende fuld pakkehash og runtime.

## Førstegangsopsætning

Dobbeltklik på `START.cmd` som almindelig bruger. `Choose-Setup.ps1` åbner
den grafiske femtrinsguide og tilbyder
**Alt på én Windows-computer** eller **Central server/VM + separat
lasercomputer**. Ved delt opsætning vælges derefter server eller lasercomputer.
Den trinvis forklarede [guide til begge opsætninger](../../docs/TO-OPSAETNINGER.md)
er indgangen for lærere og skolens IT. Brug en kontrolleret release, der indeholder denne guide; en ældre pakkes indhold ændres ikke af kildekoden.

Guiden viser netværksforslag og kan kopiere en tjekliste til IT. Den separate
station bruger en filvælger og viser skole/server fra forbindelsesfilen.
Først efter gennemgang og **Start installation** starter servervalget
`Setup-School.ps1` med administratorgodkendelse. Vælg serverens IPv4-adresse,
værkstedsnet i CIDR-format, tema og lasermodel.
Opsætningen opretter den lokale
skoleprofil, TLS-materiale, admin-identitet, planlagte opgaver og de nødvendige
firewallregler. Den private nøgle bliver på styreenheden; kun den offentlige
forbindelsesfil kan eksporteres. Samlet opsætning bruger `-Role makerspace`.
Delt opsætning bruger `-Role vm -RemoteLaserStation`, binder lærerens HTTPS
til serverens LAN-adresse og eksporterer en særskilt offentlig
`laser-station-connection.json`. Den interne lærer-backend forbliver lokal.
Server, lasercomputer og elever skal ligge i det angivne tilladte net.
Det ældre direkte kald `-Role vm` uden flag bevarer sin lokale lærerlistener;
det udgør ikke den nye delte opsætning. Resume kræver det samme flag og model.

Lasercomputervalget starter `Setup-LaserStation.ps1 -Action Install` for den
aktuelle Windows-bruger. Det bruger forbindelsesfilen og den verificerede
skolepakke, opretter ingen serveropgaver og ændrer ikke firewall. Den offentlige
CA tilføjes kun brugerens certifikatlager. `Update-LaserStation.cmd` vælger
udtrykkeligt `Update`; identitet, konfiguration og plader bevares.
`ResumeInstall` genoptager en journalført ren installation fra samme pakke.
`-Action Update -FinishSetup` færdiggør certifikat/genveje efter en fejl dér
uden at kopiere programmet igen. Se den trinvise guide for kommandoer.

Den grafiske guide viser log og kræver både kode 0 fra installationsprogrammet
og bestået lokal efterkontrol, før den melder afsluttet. Den starter ikke den
separate stations browser automatisk. Logge ligger pr. forsøg i
`%LOCALAPPDATA%\Makervaerkstedet\SetupLogs`. Netværksadgang fra andre computere
og fysisk udstyr kræver stadig særskilt afprøvning.
`START.cmd -TextMode` vælger den eksisterende tekstmenu; den grafiske guides
ekstra efterkontrol gælder kun den grafiske vej.

Til udvikling kan `Open-SetupWizard.ps1 -Preview` køres i Windows PowerShell
med `-STA`. Denne tilstand starter ikke installation eller elevation.
`scripts/render_setup_wizard.ps1` gengiver controls med syntetiske oplysninger
til PNG uden at vise et vindue eller styre brugerens skærm.

Til en på forhånd godkendt, uovervåget installation kan `Setup-School.ps1`
køres med `-AcceptInstall` og eksplicit `-SchoolName`, `-Role`, `-RoleIp`,
`-StudentSubnet` og `-Theme`. Windows' normale administratorgodkendelse gælder
stadig. Flaget springer opsætningens spørgsmål og afsluttende Enter over.
Serverens lokale porte kan vælges med `-PrintBackendPort`,
`-DesignBackendPort`, `-TeacherBackendPort` og `-TeacherPort`; standarderne
er 8765, 8766, 8767 og 8780. De skal være forskellige og må ikke bruge
gatewayens 8770–8773, tilmeldingens 8774 eller laserstationens 8790.
Skoleprofil, laserstation og genvej til kontrolpanelet bruger de valgte porte.
`-Resume` kræver samme porte og øvrige skolevalg som det første forsøg.
Det fortsætter kun det oprindelige installationsforsøg med samme pakke;
det er ikke en versionsopdatering. En Windows-server i VM bruger samme
installations- og opdateringsveje som en fysisk Windows-server.

Brug `Manage-School.cmd` til:

1. engangskode til elevcomputer;
2. SSH til en registreret computer;
3. opsætningskontrol;
4. kopi af offentlig `school-connection*.json` til en valgt USB-mappe;
5. visning af lokale lærer-/printkoder;
6. åbning af lokale lærerpaneler;
7. afmelding af en elevcomputer før geninstallation;
8. åbning af serverens kontrolpanel;
9. oprettelse og kontrol af en samlet offline backup;
10. kontrol af en eksisterende backup;
11. gendannelse til en ny, isoleret prøvemappe.

Menupunkt 7 afmelder kun elevcomputerens registrering. Det er ikke en
programafinstallation og sletter ikke elevprojekter eller køhistorik.

Administrationen kontrollerer installationens ejerskabsmarkør og bruger
Python og tilmeldingsværktøjet i den tilhørende faste installation under
`Program Files\ElevhubServer`. En anden udpakket pakkes `PackageRoot` vælger
ikke administrationskode til skolens data. Manglende eller afvigende
ejerskab, manglende programfiler og reparse points på stierne afvises.
`Manage-School.ps1 -DataRoot <datamappe> -ValidateOnly` kontrollerer denne
binding uden elevation, menu eller ændringer; det validerer ikke hele
installationen eller Windows-taskens registrering.

Udviklingskoden understøtter desuden `generation-v1`: ejerskabsmarkøren er
fortsat ankeret, mens program og data vælges samlet fra installationens
kontrolmappe. Menuen viser/læser den aktuelle generation gennem den faste
runtime. `-ValidateOnly` returnerer her også `programRoot`, `protocol` og
`revision`. Ukendt eller ufuldstændig generationsopsætning afviser opstart.

`Setup-School.ps1 -GenerationLayout` er kun en udviklingstilstand til en ny
installation med komplet udgivelsesmanifest; eksisterende pakker understøtter
den ikke. Resume kan kun fuldføre revision 1 med den oprindelige datarod.
Den faste vedligeholdelseswrapper til en eksisterende generation-v1-
installation er beskrevet i [opdateringsstatus](../../docs/MAKERSPACE-OPDATERING.md).

Laserstationens genvej med `-OpenBrowser` bruger konfigurationsfilens `port`
(standard 8790). En ugyldig port afvises før start og oprettelse af logmappe.

## Backup, opdatering og afinstallation

### Fast Windows-vedligeholdelse

**Kun til en eksisterende `generation-v1`-installation.** Eksemplerne nedenfor
kan ikke bruges til en installation med fladt layout og
udgør ikke en konvertering fra dette layout. Ren installation, opdatering
og gendannelse er særskilte handlinger.

Den installerede faste wrapper
`C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd`
kalder installationens faste runtime og accepterer handlingerne `update`,
`restore`, `rollback`, `resume`, `cancel` og `status`. Forældremapperne
`C:\ProgramData\MakervaerkstedetVedligehold` og
`C:\ProgramData\MakervaerkstedetGenerationer` skal findes og være
adminbeskyttede. Destinationsmapperne selv skal ikke findes og må ikke
overlappe; `-CompletedFolder` ved rollback er den eksisterende afsluttede
operationsmappe og er den tilsigtede undtagelse.
Hvis `-RuntimeRoot` udelades, bruges `$env:ProgramFiles\ElevhubServer`:

```powershell
& 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd' -Action update `
  -RuntimeRoot 'C:\Program Files\ElevhubServer' `
  -Folder 'C:\ProgramData\MakervaerkstedetVedligehold\opdatering-01' `
  -MaterializedFolder 'C:\ProgramData\MakervaerkstedetGenerationer\generation-ny' `
  -ProgramRoot 'D:\Kandidater\program'

& 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd' -Action restore `
  -RuntimeRoot 'C:\Program Files\ElevhubServer' `
  -Folder 'C:\ProgramData\MakervaerkstedetVedligehold\gendannelse-01' `
  -MaterializedFolder 'C:\ProgramData\MakervaerkstedetGenerationer\generation-gendannet' `
  -ProgramRoot 'D:\Kandidater\program' `
  -BackupSource 'E:\Beskyttet-backup\makerspace-backup-01'

& 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd' -Action rollback `
  -RuntimeRoot 'C:\Program Files\ElevhubServer' `
  -Folder 'C:\ProgramData\MakervaerkstedetVedligehold\tilbagefoersel-01' `
  -MaterializedFolder 'C:\ProgramData\MakervaerkstedetGenerationer\generation-tilbage' `
  -CompletedFolder 'C:\ProgramData\MakervaerkstedetVedligehold\opdatering-01'
```

Fortsæt eller undersøg en eksisterende journal med kun dens mappe:

```powershell
& 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd' -Action resume -RuntimeRoot 'C:\Program Files\ElevhubServer' -Folder 'C:\ProgramData\MakervaerkstedetVedligehold\opdatering-01'
& 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd' -Action cancel -RuntimeRoot 'C:\Program Files\ElevhubServer' -Folder 'C:\ProgramData\MakervaerkstedetVedligehold\opdatering-01'
& 'C:\Program Files\ElevhubServer\deployment\school-setup\Maintain-School.cmd' -Action status -RuntimeRoot 'C:\Program Files\ElevhubServer' -Folder 'C:\ProgramData\MakervaerkstedetVedligehold\opdatering-01'
```

`restore` kræver en verificeret historisk backup fra samme installation og
skole, inklusive matchende ejerskab, netværkskontrakt og CA. Den nuværende
generation og historikken bevares. `rollback` bruger tidligere programkode
sammen med aktuelle data og en ny datakopi. Operatoren gemmer og gendanner
tilstanden for `ElevhubServer` og `ElevhubSchoolEnrollment`, kontrollerer
task-ejerskab og opretter kontrollerede ACL'er på den nye generation.
`-SettledFolder` må kun tilføjes ved rollback, når en afsluttet annullering
skal dokumentere samme program/data med højere revision.
`status` viser journalfase og vedligeholdelsesspærre; `queues_unpaused` er
`false`, og printkøen åbnes ikke automatisk.

Generationsforløbet kræver særskilt Windows-afprøvning af installation,
rettigheder, opgavestart, restore og afinstallation. Wrapperen gendanner ikke
en mistet vært; erstatningsværten er en særskilt gendannelsesvej.

I udviklingskoden samler menupunkt 9 serverens hub-, print- og laserdata fra
den installerede skoleopsætning. Det omfatter filer, workflows, køer,
printeropsætning, lærerlogin, tilmeldinger og private nøgler. Du behøver ikke
skrive en JSON-profil for det understøttede standardlayout. Generationslayout
bruger de aktuelt valgte data; andre udpakkede pakker vælger ikke kilden.

Gem arbejde og stop server, tilmelding og lokal laserstation før backup.
Værktøjet stopper eller genstarter ingen tjenester. En kørende tjeneste eller
ufuldstændig datakontrakt afvises, før backupdestinationen oprettes. Eksterne
backends kræver fortsat en eksplicit profil i det avancerede backupværktøj;
menuen udelader dem ikke stiltiende.

Vælg en **ny lokal mappe**, hvis forældremappe findes. Backupen får privat
adgang og kontrolleres efter kopiering. Den indeholder nøgler og elevdata:
kopiér den efterfølgende til skolens beskyttede backupmedie, og følg skolens
opbevaringsplan. En kopi på serverens egen disk beskytter ikke mod diskhavari.
Menupunkt 10 kontrollerer senere kopiens filer, kontrolsummer og databaser.

Menupunkt 11 gendanner i en anden ny mappe og erstatter ikke driftsdata.
Kopien beholder sine opstartsspærrer, og printkøen er pauset. En godkendt
gendannelsesprøve er ikke det samme som en aktiveret erstatningsserver.
Slet ikke holdmarkørerne manuelt. Ved en afbrudt kopiering bevares destinationen
til eftersyn; et nyt forsøg skal bruge en ny mappe.

Se [backupvejledningen](../../docs/MAKERSPACE-BACKUP.md) og
[installationsreparation](../../docs/INSTALLATIONSREPARATION.md).
Disse funktioner er endnu ikke del af en offentlig eller udrullet
installationspakke.

`Remove-School.cmd` bruger den installerede ElevhubServer-fjerner i
`server/fjern.ps1`. Den kræver administrator og bekræftelse. Første
`generation-v1`-installation kan fjerne programmet efter ejerskabskontrol.
Ved senere generationer afinstallerer den ejerskabskontrolleret Windows-
integration, tasks, firewallregler, genveje og CA, mens alle programmer,
kontrolkvitteringer og data bevares som arkiv. `Remove-School.ps1 -WhatIf`
viser den planlagte fjernelse.

En opdatering må ikke ændre den aktive printeropsætning. Der findes fortsat
ingen offentlig eller udrullet samlet pakke.
