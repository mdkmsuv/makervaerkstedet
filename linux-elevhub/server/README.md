# Server-VM til Printværkstedet (Windows 11)

Denne mappe indeholder den fælles Elevhub-server og lærerpanel. Windows-serveren kan køre fysisk eller i en VM; se [installationsvejledningen](../../docs/KOM-GODT-IGANG.md).

Serveren samler Elevhubben og de centrale værksteder. 3D-print kører på TCP
8770, laser på 8771 og folie på 8772. Elevhub-status ligger på 8773 og
lærerpanel på 8780. Scratch, Datatræneren og Kredsløb er elevcomputer-apps:
de leveres af `roles/kiosk` og lytter kun på loopback på hver elevcomputer
(8792, 8793 og 8794; projektarkivet bruger 8791). De lokale porte bindes ikke
af den centrale server. Internet er en separat browserapp og har ingen egen serverport. Printværkstedets
og Designværkstedets egne processer kører bag elevindgangene. Følg
sikkerhedsvejledningen ved lokal installation.

Scratch- og Datatræner-builds kopieres fra controllerens konfigurerbare
`kiosk_lokale_app_kilder` til elevcomputerens lokale app-root sammen med
Kredsløb. Elevhub-konfigurationens lokale apps bruger derfor
`lokal_service: true` og kræver ikke en central `mappe` eller statisk
serverkopi. Se [Scratch-noterne](scratch/README.md) og
[Datatræner-noterne](datatraener/README.md).

Lærerens `Åbn alle` og `Luk alle` gælder alle værksteder, inklusive Scratch og
webværksteder, men aldrig Internet. En lukning varer to timer og bevarer sin
oprindelige frist efter genstart.

Elevhub-serveren skal bruge HTTPS og computeridentitet. Brug separate testdata ved lokal afprøvning; den gamle HTTP-konfiguration må ikke bruges. Se [SIKKERHED.md](../SIKKERHED.md). Backup og produktionsinstallation skal konfigureres særskilt.

## Størrelse

| | Minimum | Anbefalet |
|---|---|---|
| vCPU | 4 | 6–8 |
| RAM | 8 GB, fast tildelt | **12 GB**, fast tildelt. 16 GB, hvis MeshCentral og flere værksteder også skal køre her. |
| Disk | 100 GB | 128 GB på SSD |
| GPU | Ikke nødvendig | Dimensionér efter værksteder og lagskæring. |

Dimensionér efter samtidige brugere og de faktisk aktiverede tjenester.
Mål lagskæring og spidsbelastning på serverens udstyr.

## VM-indstillinger

- Brug en RAM-tildeling, som passer til den målte belastning.
- Windows 11 kræver UEFI, Secure Boot og vTPM.
- VM'en skal starte automatisk med værten, og Windows må aldrig gå i dvale. Læg Windows Updates genstarter uden for skoletiden.
- **Netværk:** tilslut serveren til makerspacets eget net med et fast navn og en reserveret adresse. Elevcomputerne, administrationen og maskinerne kan ligge i adskilte områder med eksplicitte firewallregler; se [installationsvejledningen](../../docs/KOM-GODT-IGANG.md). Printeropdagelse via multicast kan kræve samme netsegment eller manuel registrering, afhængigt af udstyret.

| Retning | Port | Formål |
|---|---|---|
| Ud til printerne | TCP 8883 (MQTT/TLS), TCP 990 + dataporte (FTPS), TCP 6000 (kamera) | Status, printfil, pladebilleder |
| Ud (kun ved opsætning) | UDP 1990/2021 multicast 239.255.255.250 | Printeren findes (SSDP) |
| Ind fra elevcomputerne | **TCP 8770–8773** | Elevindgange for print, laser, folie og Elevhub-status |
| Ind fra lærerens pc | TCP 8780 | Lærerpanel med login |
| Kun internt på serveren | TCP 8765–8767 | Printværkstedet og Designværkstedet selv |

## Lokal udvikling og afprøvning

Fra projektroden på udviklingscomputeren:

```powershell
powershell -ExecutionPolicy Bypass -File .\linux-elevhub\testmiljoe\vaert\start-server-test.ps1 -CheckOnly
powershell -ExecutionPolicy Bypass -File .\linux-elevhub\testmiljoe\vaert\start-server-test.ps1
```

`-CheckOnly` kontrollerer testfiler, kommandoer og porte uden at starte processer. Normal start bruger `<LOKAL-TESTMAPPE>\` til isolerede data og en simuleret printer. Det er en udviklingsstarter, ikke en installationspakke til skolens server. `-Stop` stopper kun verificerede testprocesser. Gamle processer med en anden kommandolinje kan give en portkonflikt; de overtages eller stoppes ikke automatisk.

Den oprindelige `testmiljoe/vm/send-testprint.py` er skrevet til printkøen. Værkstedet skal være åbnet i lærerpanelet, hvis kontrollen af læreradgang skal forventes at give 403 frem for 423 (lukket værksted). Brug `--uden-print` til adgangsprøven; udelades flaget mod en rigtig kø, kan testen starte et fysisk print.

Automatiske regressioner bruger egne servere på ledige porte og starter ingen fysisk maskine.

## Installation og vedligeholdelse

Brug [skolepakkens guide](../../docs/KOM-GODT-IGANG.md) til ren installation
og [serveropdatering](../../docs/MAKERSPACE-OPDATERING.md) til en eksisterende
installation. De to indgange må ikke byttes om.

Backendprocesserne på 8765–8767 holdes lokale eller afskærmes med firewall.
Konfigurér elevcomputerne til den faktiske serveradresse og skolens CA.
Brug kun én aktiv kø for de samme maskiner.

Backup skal dække serverens faktiske hub-, print- og laserdata samt nøgler og
opsætning; se [backupvejledningen](../../docs/MAKERSPACE-BACKUP.md). Restic-
rollen til elevprojekter er ikke i sig selv backup af Windows-serveren.
Kontrollér TLS, rettigheder, opgaver, data og skolens udstyr efter installation.

## Verifikation

Fra projektroden på Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s linux-elevhub/tests
.\.venv\Scripts\python.exe -m unittest discover -s linux-elevhub/server/elevhub
powershell -ExecutionPolicy Bypass -File .\linux-elevhub\testmiljoe\vaert\test-start-server-test.ps1
```

Kommandoerne er testinstruktioner; denne dokumentation opgiver ikke et testantal eller et resultat.
