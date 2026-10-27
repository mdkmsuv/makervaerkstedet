# Samlet elevinstallation og opdateringer over SSH

> Offentlig dokumentation: LOKAL_IP er en pladsholder. Brug skolens egne adresser.

Installationsforløbet er udvidet, så en fælles USB-ISO kan medbringe hele
elevsystemet: CaDoodle med Kloster-udvidelsen og guideprojekterne, elevskærmen,
projektmapper, Scratch, Datatræneren, Kredsløb, laser, folie og kassegenerator.
Windows-serveren med printkø og lærerfunktioner forbliver en separat server.

## Fælles installations-USB

Samme installations-USB kan bruges til flere computere. Inden partitionering
vælges computerens navn på en liste, og valget bekræftes. Brug hvert navn på
én fysisk computer; vælg samme navn igen ved geninstallation af den computer.
USB'en registrerer ikke automatisk, hvilke navne der allerede er taget.

Den valgte computers navn og egne Elevhub-adgangsværdier følges ad. Kun den
valgte profil kopieres til den installerede computer. Skoleserveren skal kende
alle profilerne på forhånd; der er ingen deling af én elevidentitet mellem
maskiner og ingen afhængighed af serverkontakt for selve navnevalget.

Den private byggeprofil kan nu bruge `device_files` med en liste af eksisterende
enhedsfiler i stedet for `device_file`. `ca_file`, `admin_public_key`, `wifi` og
`local_admin` er fælles. Navne og begge adgangsværdier skal være unikke.
De tidligere personlige profiler virker fortsat.

Den nye skoleudgave har navnene **<ELEV-VM>–vaerksted-12**, Wi-Fi
**IOT-KLSTR**, dansk pc105-tastatur i installation, adminterminal og grafisk
session samt lokal admin. Wi-Fi-koden ligger kun i den private profil/ISO.
Tastaturpakker og konsolcache følger med fra installationen.

Elevbjælken viser batteriprocent og strømstatus. Den advarer ved højst 20 %
og tydeligere ved højst 5 %, når strømmen ikke er tilsluttet. Ukendt eller
manglende batteridata bliver ikke vist som et opdigtet batteriniveau.
Advarslerne lukker ikke programmer og kan ikke forhindre et fysisk strømsvigt.

## Forløbet på elevcomputeren

### Rolig opstart efter klargøring

Kioskrollen skjuler elevens automatiske loginhilsen og dæmper normale
kernel-/systemdbeskeder. GRUBs gendannelsesmulighed og administrative
loginterminaler bevares. `console-setup` installeres, så det danske layout
også kan aktiveres i admin-terminalen.

X-sessionen viser **Værkstedet starter** i skolens lyse hovedstil med
Metrophobic og bordeaux accent. Skærmen fjernes, når browserens første
katalog er tegnet, elevbjælken er synlig, og de konfigurerede lokale
health-adresser svarer. Der kontrolleres ingen ekstern adresse i denne
opstartsskærm; startsidens eksisterende offlineforløb åbner kataloget ved
netudfald. Browseren kan melde sig klar, selv hvis den sænker billedhastigheden
bag ventebilledet.

Efter 45 sekunder tilbydes **Prøv igen** og **Åbn værkstedet**, så en lokal
fejl ikke efterlader en endeløs venteskærm. Ingen af knapperne stopper apps.
Filer og bootkonfiguration kan opdateres mens eleven arbejder; den nye
opstart ses ved næste almindelige genstart. Firmware- og egentlige
bootfejl kan stadig vise tekst. Eksisterende ISO'er ændres ikke af kildefilerne.

### Installation

1. USB-installationen installerer Debian, SSH, Python og Ansible. Den kopierer
   softwarepakken og den valgte computers eksisterende skoleprofil fra USB-mediet.
2. Når installationen har slukket, fjernes USB-mediet, og computeren tændes.
3. Systemtjenesten `elevhub-firstboot` installerer elevsystemet automatisk.
   CaDoodle pakkes ud uden en manuel første åbning. Elevskærmen starter først,
   når hele opskriften er gennemført.
4. SSH er tilgængelig under klargøringen og ved senere opstarter. En fejl
   gemmes i journalen, og klargøringen forsøges igen efter 60 sekunder. En
   færdig installation køres ikke igen ved hver opstart.

Der kræves stadig internet under installationen til Debians pakker.
De store programfiler og lokale webværksteder ligger på USB'en. Dette er
ikke en fuldstændig offline-installation af Debian.

Installationsmediet sletter den valgte interne disk. Den samlede ISO vælger
kun automatisk, når præcis én skrivbar intern disk findes. USB-diske og
installationsmediet udelukkes også, når USB-controlleren melder disken som
ikke-flytbar. Flere interne diske eller et ukendt installationsmedie stopper
installationen før partitionering; der gættes ikke på `/dev/sda`.
Fra R6 viser et tidligt partman-hook årsagen og vender tilbage til
hovedmenuen. Fra R8 indeholder fejlskærmen også et dansk råd og et inventar
over alle diske (størrelse, bus, flytbar/skrivebeskyttet, model), PCI-
diskcontrollere og installationsmediet. Ser vælgeren en Intel-controller i
RAID/RST-tilstand (PCI-klasse 0x0104), rådes der direkte til at skifte
SATA-tilstand til AHCI i BIOS. Et foto af skærmen er derfor nok til at
fejlsøge en ukendt computer. Hooket ligger direkte i initrd og kører før Debians øvrige
diskinitialisering. Det må ikke flyttes til `partman/early_command`, fordi
Debians `preseed_command` opfanger fejlstatus og ellers fortsætter.
En personlig ISO indeholder én computers adgang; en fælles ISO indeholder
adgangen for alle navnene på listen. Begge skal opbevares som
administrationsmateriale. Ingen private SSH-nøgler eller servernøgler kommer
med på elevmediet.

## Før udgivelse

Kontrollér den byggede ISO og første tilmelding på det relevante Linux-udstyr.
En opdatering af en eksisterende computer skal bevare identitet, skoleprofil
og projekter og bruge den særskilte opdateringsvej.
Private adresser, SSH-fingeraftryk og skoleprofiler er lokale inputs.

## Bygning på administrationscomputeren

En privat maskinprofil kan indeholde `local_admin` med `username` (fx `makeradmin`)
og `password_hash` (SHA-512 eller yescrypt). Så oprettes den lokale administratorkonto
og Admin-knappen under installationen. Brug kun hashen her, aldrig klar adgangskode.
Admin åbner en lokal terminal med login; Ctrl+Alt+F1 vender tilbage til værkstedet.

`build.py bundle` laver en genbrugelig softwarepakke uden credentials.
Filoversigten er eksplicit: projektets rod, serverhemmeligheder og private
nøgler kopieres ikke. CaDoodles officielle `.deb` kontrolleres mod den
pinnede SHA256. Alle pakkens filer får desuden en SHA256 i `manifest.json`.

Eksempel på udviklerkørsel i WSL, med de eksisterende lokale artefakter:

```sh
python3 linux-elevhub/installation/build.py bundle \
  --output output/linux-elevhub/software \
  --deb .runtime/downloads/linux-elevhub/CaDoodle-1.9.6-amd64.deb \
  --bootstrap /sti/til/CaDoodle-ApplicationInstall.zip \
  --kloster /sti/til/CaDoodle-Kloster.jar \
  --scratch /mnt/d/Elevhub-VM/scratch/scratch-gui-v15.1.1-local-projects-build \
  --datatraener /sti/til/datatraener-build
```

En profil er en JSON-fil med stier til den eksisterende skoleprovisionering:

```json
{
  "server": "LOKAL_IP",
  "ca_file": "sikkerhed/ca.crt",
  "device_file": "sikkerhed/enheder/<ELEV-VM>.json",
  "admin_public_key": "/home/elevhub/.ssh/elevhub_ed25519.pub"
}
```

Computernavnet kommer fra den valgte enhedsfil. Serveren skal allerede kende
identiteten. Byg ISO'en i en privat WSL-mappe med mode 700; byggeprogrammet
afviser delte `/mnt/`-mapper til de personlige ISO-filer:

```sh
python3 linux-elevhub/installation/build.py iso \
  --bundle output/linux-elevhub/software \
  --profile /home/elevhub/skole/<ELEV-VM>.json \
  --base-iso /mnt/d/Elevhub-VM/iso/debian-13.7.0-amd64-netinst.iso \
  --output /home/elevhub/skole/iso/<ELEV-VM>.iso
```

Til en Wi-Fi-installation kan den private profil også have feltet `wifi`
med `ssid` og `password`. Disse værdier lægges kun i den personlige ISO's
installationssvarfil, aldrig i den genbrugelige softwarepakke. Profil og
ISO skal opbevares privat; USB-mediet indeholder netværksadgangen.
Uden feltet spørger installationen selv om Wi-Fi-netværk og kode.
Debian kan stadig vise listen over fundne netværk: vælg det oplyste netværk.
Den forudfyldte WPA-kode bruges derefter uden manuel indtastning.

Til store pakker kan `TMPDIR` pege på en privat mappe på WSL-disken, så
bygningen ikke fylder WSL's RAM-baserede `/tmp`.

Kontrollér også ledig plads på det Windows-drev, som WSL's VHD ligger på:
Linux' `df` viser den virtuelle kapacitet og afslører ikke et næsten fyldt
værtsdrev. ISO-bygning, midlertidige kopier og en komplet test-VM kræver
betydeligt mere plads end den færdige ISO. Hav mindst 30 GB fri på
værtsdrevet før hele bygge- og testforløbet. På denne maskine er Debian-WSL
flyttet til det private lager på D-drevet.

## Opdatering uden geninstallation

Linux-elevcomputerens opdateringsvej er adskilt fra ren installation med ISO.
Det gælder også en Linux-elev-VM. En Windows-server i VM bruger i stedet
Windows-serverens egne veje. Se [de fire produktveje](../../docs/KOM-GODT-IGANG.md#vælg-først-ren-installation-eller-opdatering).

Åbn [Windows-administrationen](../administration/README.md), vælg skolens
computerliste og derefter **Tjek ændringer** eller **Anvend opdatering**.
Vælg **CaDoodle** for kun 3D-programmet eller **Hele elevsystemet** for hele
opsætningen. Der skrives ingen kommandoer på elevcomputeren.

Administrationen bruger den eksisterende SSH-nøgle og Ansible. Computerlisten
`ansible/inventory/skole/fra-serveren.py` henter de tændte computere og deres
SSH-værtsnøgler fra serveren (se [administrationen](../administration/README.md)).
Faste IP'er er derfor ikke nødvendige. Værtskontrollen slås ikke fra.
Hvis CaDoodle er åbent, springer CaDoodle-rollen hele sin opdatering over.
En elevskærm, der er i brug, genstartes ikke af en opdatering; ændringen
virker fra næste opstart.

Elevernes projekter indgår ikke i softwarekopieringen. Hele elevsystemets
opskrift skriver dog enhedsoplysninger, skolekonfiguration, CA og SSH-adgang
fra administrationscomputerens provisionering. Brug den korrekte eksisterende
skoleopsætning, og kontrollér identitet og adgang før og efter opdatering.

Dette er en eksisterende SSH-/Ansible-administrationsvej, ikke dokumentation
for en komplet versionslåst releaseopdatering. Den færdige opdateringsvej
skal bevare projekter, tilmelding og skolevalg, angive kilde-/målversion
samt tydeligt rapportere komponenter, der er sprunget over. Afbrydelse,
genoptagelse og fejl skal efterprøves særskilt. En opdatering skal afprøves
på én computer før udrulning til alle; ISO'en bruges til ren installation.

## Wi-Fi under installation

Kontrollér netværk og trådløs firmware på den konkrete elevcomputer. En
virkende VM er ikke bevis for USB-opstart eller Wi-Fi på fysisk udstyr.

## Kontroller

- `linux-elevhub/tests/test_full_installation.py`: profiler, pakkeintegritet,
  svarfil samt første opstarts succes, fejl og gentagelse med simulerede
  systemkald.
- `linux-elevhub/tests/test_cadoodle_bootstrap.py`: sikker udpakning og
  bevarelse af en eksisterende installation.
- `linux-elevhub/tests/test_ssh_bootstrap.py`: SSH-opstart og fejlforløb.
- `linux-elevhub/tests/test_install_disk_selection.py`: valg af intern disk,
  NVMe/SATA, USB med removable=0, flere diske og ukendt installationsmedie.
- Ansible `--syntax-check`: opskrifterne parses uden at ændre en maskine.

Disse kontroller erstatter ikke prøveinstallation på en fysisk Linux-computer
eller VM, rigtig SSH-login og brugerens skærmbillede af elevskærmen.
