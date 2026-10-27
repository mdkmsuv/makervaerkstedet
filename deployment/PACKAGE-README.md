# Installér Makerværkstedet

**Kreditering: Klostermarksskolen.**

Start med [én eller to computere](../docs/TO-OPSAETNINGER.md). Den gennemgår
den grafiske installationsguide trin for trin, herunder hvor oplysningerne
findes. [Kom godt i gang](../docs/KOM-GODT-IGANG.md)
supplerer med elevtilmelding, maskiner og prøver; følg altid vejledningen fra den konkrete releasepakke.

Pakken er til en Windows x64-computer eller server på værkstedets separate
LAN. Læreren styrer maskiner, workflows, digitale værksteder og elevcomputere
fra kontrolpanelet. Systemet kan bruges uden tilmeldte elevcomputere.
Elevværkstederne kører kun på Linux; pakken er ikke en Windows-elevklient.

1. Udpak hele pakken i en ny mappe.
2. Dobbeltklik på `START.cmd`. Vælg **Alt på én Windows-computer** eller
   **Central server/VM + separat lasercomputer**. Ved delt opsætning vælges
   først serveren, som opretter `laser-station-connection.json`; på
   lasercomputeren vælges stationen, som bruger denne fil. Kun serverdelen
   kræver administratoradgang. Stationen installeres som den daglige bruger.
   Guiden foreslår netværksoplysninger, forklarer felterne og kan kopiere en
   tjekliste til IT. Gennemgå valgene før **Start installation**; først dér
   beder serverdelen om Windows-godkendelse. Vent på efterkontrollens resultat.
3. Åbn genvejen **Makerværkstedet**. Første gang på serveren vælger læreren en
   lærerkode på mindst otte tegn; den bruges derefter til kontrolpanelet.
4. Brug `Manage-School.cmd` til elevtilmelding, offentlig skolefil,
   særskilte printer-/laserkoder og vedligeholdelse. En IT-medarbejder kan
   frivilligt køre `Setup-School.ps1 -CheckOnly` før `START.cmd`.
   Følg [skoleopsætning](school-setup/README.md). En separat station har desuden
   genvejen **Laserstation** og opdateres med `Update-LaserStation.cmd`.

`START.cmd` opretter en ny installation. Serveropdatering følger sin egen
formatkontrollerede vej; en stationsopdatering opdaterer ikke hovedsystemet.

Tilføj understøttede maskiner og workflows i lærerens `/makerspace`-panel.
En manuelt registreret maskine får ikke automatisk en hardwaredriver.
Linux-elevcomputere tilmeldes med hver sin identitet og skolens offentlige
CA; private nøgler kopieres ikke til eleverne. Deres projekter ligger lokalt,
mens afleveringer og lærerens valg ligger på serveren.

Læs [kontrolpanel](../docs/SERVERKONTROLPANEL.md), [backup](../docs/MAKERSPACE-BACKUP.md),
[opdatering og tilbageførsel](../docs/MAKERSPACE-OPDATERING.md) og
[kodeagentens indgang](../docs/AGENTINDGANG.md).
En kodeagent tilpasser systemets kildekode gennem Markdown-vejledningerne.
Daglig agentstyring og særskilte agentnøgler indgår ikke i produktet.

Programmets licens står i [LICENSE-SCOPE.md](../LICENSE-SCOPE.md) og [LICENSE](../LICENSE).
Komponenternes notices følger deres egne vilkår. Skolenavnet kan ændres;
ophavskrediteringen af Klostermarksskolen skal bevares.
