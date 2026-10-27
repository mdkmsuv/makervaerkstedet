# CaDoodle-opdatering til eksisterende elevcomputere

Denne pakke udskifter kun CaDoodles JAR-fil. Runtime, launcher, projekter, printerindstillinger og øvrige elevdata bliver på plads. Luk CaDoodle normalt før installationen; installerne afviser et kørende program og starter det ikke igen.

Opdateringens `release_id` er `2026.10.06.17`. CaDoodles kompatibilitetsversion i manifestet er fortsat `0.46.094`; det er to forskellige versionsnumre.

Opdatering 17 bevarer 22,5°-trinnene og fortsætter drejningen, når markøren
rammer håndtagets andre lag. Forsinkede kamerakald kan ikke genvise håndtag
efter fravalg. Linux-versionen understøtter skjult forstart; den separate
prewarm-pakke kobler den til lærerens åbning af 3D-værkstedet.

Opdatering 16 samler alle tidligere visuelle og funktionelle rettelser. Tekst-
og tilstandsparametre gemmes i rækkefølge ligesom talfelter. Enter/Tab gemmer,
Escape gendanner feltets senest accepterede værdi, og låste felter ignoreres.
Egenskabspanelets felter er større, lange navne ombrydes, og indholdet kan
rulles på korte skærme. Den særskilte Linux-startopdatering installeres ved siden af.

Opdatering 15 retter de avancerede talfelter: komma og punktum virker i både
frie felter og valglister, Enter/Tab og fokusflytning gemmer værdien, og ugyldige
tal eller annullering ændrer ikke modellen. Uændrede tal starter ingen ny
beregning. Hurtige parameterændringer udføres i rækkefølge, så modellen ender
med det senest accepterede tal. Låste figurers parameterpanel kan ikke redigeres.
Valglisten følger manuelt indtastede tal, så man også kan vælge den oprindelige
værdi igen. Alle rettelser fra opdatering 14 er med.

Opdatering 14 retter valg af flade med touch: et tryk bruger sin egen placering,
og gamle bevægelser efter annullering ignoreres. Afrund, Bøj og Forlæng fastholder
de figurer og den placering, der blev valgt ved trykket, og udføres i rækkefølge
med andre modelændringer. En færdig beregning overtager ikke en nyere markering.
Mønsterkopiering og Indsæt opdaterer markering og kopiliste samlet, og
mønsterværktøjerne afviser tom markering. Alle rettelser fra udgave 13 er med.

Opdatering 13 gør de ekstra værktøjer lettere at kende på touch: ikonerne har
nu synlige danske navne som Mønster, Bøj og Afrund. Rækken vises fortsat kun
i avanceret tilstand. “Snap-gitter” hedder nu “Trin”. Skolens hovedudtryk med
Metrophobic, lyse grå/hvide flader og bordeaux markeringer er bevaret.
Alle rettelser fra opdatering 12 er med.

Spejl reagerer kun på venstreklik eller touch. Afsluttede spejlberegninger
kan ikke genskabe en gammel forhåndsvisning efter annullering eller skift af
markering. Spejlklik gemmer deres egne valgte figurer og arbejdsplan og udføres
i samme rækkefølge som de øvrige modelændringer og Fortryd/Gentag.

Opdatering 12 retter korte træk på figurer: et træk, der har startet en flytning,
må ikke bagefter blive tolket som et nyt tryk og fravælge figuren. Første tryk
markerer, næste tryk på samme figur fravælger, og tredje tryk markerer igen.
Alle ændringer fra opdatering 11 er med.

Opdatering 11 er en testudgave med Linux som hovedplatform. Den tilføjer større knapper og figurfelter, tydeligere tekst, indbygget hjælp og touchbetjening: tryk på en figur for at markere den, tryk igen for at fravælge den, træk med én finger på tom plade for at dreje visningen, og brug to fingre til zoom. **Vælg flere** markerer flere figurer uden tastatur. **Drej visning** og **Flyt visning** kan også bruges oven på en figur. **Byg** går tilbage til redigering. Rotation, der afbrydes af en anden gestus, må ikke gemmes senere ved et slip.

Rettelserne fra opdatering 10 er med: komma og punktum i talfelter, hurtige X/Z-ændringer, lettere resize-preview, Juster-status samt gemning før lukning. Opdateringen skal stadig kontrolleres på en fysisk Linux-elevcomputer med touch; automatiske input- og layouttests er ikke en godkendelse af en bestemt touchdriver eller målgruppens oplevelse.

Opdel bevarer nu den tidligere gruppe i historikken, så Fortryd ikke også viser
gruppens enkelte dele oven i gruppen.
Fortryd/Gentag venter på den seneste modelændring, også ved hurtige tryk.
Slet, Gruppér, Opdel og egenskabsændringer udføres også i rækkefølge.

## Byg pakken på administrationscomputeren

Brug de færdigbyggede Windows- og Linux-JAR-filer. Vælg en **ny** outputmappe hver gang:

```powershell
python build_update.py --windows-jar "sti\til\windows\CaDoodle-Kloster.jar" --linux-jar "sti\til\linux\CaDoodle-Kloster.jar" --out "output\cadoodle-update"
```

Builderen laver en samlet mappe, en samlet ZIP samt særskilte `-windows.zip` og `-linux.zip`. Udpak hele den relevante ZIP på elevcomputeren, så installationsscriptet ligger ved siden af `windows/` eller `linux/`. `SHA256SUMS.txt` indeholder filernes checksums; manifestet kontrolleres igen under installationen.

## Windows

Standardinstallation: `C:\Program Files\ElevhubClient\native-apps\cadoodle\app\CaDoodle-Kloster.jar`.

1. Udpak **hele** Windows-ZIP'en. Lad `Install-Windows.ps1`, `Start-Update.cmd` og mappen `windows` blive sammen.
2. Dobbeltklik `Start-Update.cmd`, og godkend Windows' administratorprompt. Programmet skal være lukket.
3. Ved kontrol uden ændringer eller tilbagerulning: åbn PowerShell som administrator i den udpakkede mappe og brug:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-Windows.ps1 -DryRun
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-Windows.ps1 -Rollback
```

Windows-installeren kræver ikke Python på elevcomputeren. En tilpasset installation kan angive `-InstallRoot`, `-Package` og `-StateDir`.

## Linux

Standardinstallation: `/home/elev/bin/CaDoodle-ApplicationInstall/0.46.094/CaDoodle-Application.jar`. Den tilsvarende cache er `/var/cache/elevhub/CaDoodle-Kloster.jar`.

1. Udpak **hele** Linux-ZIP'en. Lad `install_linux.py` og mappen `linux` blive sammen.
2. Kør som root fra den udpakkede mappe:

```sh
sudo python3 ./install_linux.py --package ./linux --dry-run
sudo python3 ./install_linux.py --package ./linux
sudo python3 ./install_linux.py --rollback
```

Linux-installeren holder launcherens lås ved `/home/elev/.local/state/elevhub/cadoodle.lock` under kontrol og udskiftning. Den gemmer separate, hashkontrollerede kopier af installations-JAR og eventuel cache. En cache, der oprindeligt manglede, bliver fjernet ved rollback. Til test eller tilpassede installationer findes `--install-root`, `--cache`, `--lock` og `--state-dir`.

Tilbagerulning bruger kun den aktive installationskvittering og stopper, hvis den aktuelle JAR eller cache er ændret siden opdateringen. Kvitteringer og backups ligger som standard i `/var/lib/elevhub/cadoodle-update` på Linux og `%ProgramData%\ElevhubClient\cadoodle-update` på Windows. Behold mappen, indtil opdateringen er godkendt på maskinen.
