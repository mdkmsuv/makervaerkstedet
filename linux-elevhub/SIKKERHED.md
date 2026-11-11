# Software til sikker drift

Sikkerhedsdelene skal bruge HTTPS og computeridentitet. Windows-browserens CA-tillid og den fysiske udrulning skal kontrolleres på skolens udstyr. Netværkskravene er beskrevet i [installationsvejledningen](../docs/KOM-GODT-IGANG.md).

## Hvad koden understøtter

- **HTTPS** på Elevhubbens elevindgange, statusserver og lærerpanel. Certifikater valideres mod en lokal CA. Serveren nægter normal opstart uden certifikat, nøgle og enhedsregister. Der er ingen automatisk HTTP-fallback.
- **To identiteter pr. computer.** Den beskyttede Linux-tjeneste har et token i en root-ejet fil med mode 0600. Browser og elevbjælke har et andet token, som kun giver adgang til at melde egen visning. Serveren gemmer token-hashes. UI-tokenet kan ikke kvittere for regler, oprette en computer som online eller forlænge dens online-status.
- **Kontrolleret udskiftning af nøgler.** Provisionering opretter nye mapper og nægter overskrivning. Enhedscredentials kan tilføjes eller roteres særskilt. Serveren genlæser registeret ved godkendelse, så ændringen gælder uden genstart.
- **Åbent ved mistet serverkontakt.** Ved netudfald åbnes alle lokale værksteder, CaDoodle og Internet, også ved kold opstart. Gamle lukninger og skærmpauser bevares ikke under udfaldet. Ved genforbindelse anvendes lærerens aktuelle valg. Det stopper ikke igangværende maskinjob.
- **Mere afgrænset serverdrift.** TLS-handshakes ligger i begrænsede workers, forbindelser har timeout, og lærerens cookie har Secure ved HTTPS. Værkstedernes egne backendporte skal være på loopback. Lærerpanelet bindes som standard til loopback; netværksadgang kræver et eksplicit adressevalg og firewallopsætning.
- **Opdatering og SSH.** Ansible sætter daglige Debian-sikkerhedsopdateringer uden automatisk genstart. SSH kræver stadig en nøgle; agentforwarding, X11-forwarding og tunnelenheder er slået fra. `grundsystem_admin_kilder` kan begrænse adgang til konkrete lærer-/administrationsadresser.
- **Backup med restic.** En særskilt rolle kan tage versionsbackup til et valgt lokalt eller monteret mål. Den er slået fra, indtil mål og hemmelighed er konfigureret. Se [backupvejledningen](backup/README.md). Der er ikke indført et forbud mod at slette elevens aktuelle arbejdsfiler.

Elever må slette arbejdsfiler; gendannelse kræver beskyttede backupversioner. Backup kan kun genskabe indhold, der er med i en vellykket kopi. Hovedsystemet kan køre samlet på én Windows-computer eller på en central Windows-server med separat lasercomputer; se [de to opsætninger](../docs/TO-OPSAETNINGER.md). Ved serverkontakt gælder lærerens adgangsvalg. Uden serverkontakt er værksteder og Internet åbne. Hver værkstedslukning udløber efter to timer eller ved tidligere åbning fra læreren. Fristen gemmes og bevares ved genstart. Internet åbnes ikke automatisk og nulstilles til lukket ved genstart af Elevhub-serveren.

## Første sikre pilot

Arbejdet kan forberedes uden skolens netværk. Brug navne/adresser, der faktisk skal bruges i den valgte pilot, i certifikatets SAN-felter. En senere adresseændring kan kræve et nyt certifikat.

### 1. Provisionér i Debian WSL

Fra linux-elevhub-mappen i WSL; placér hemmeligheder i Linux-hjemmemappen, hvor mode 700/600 kan håndhæves:

```sh
python3 server/elevhub/opret_sikkerhed.py init \
  --output-dir "$HOME/elevhub-sikkerhed" \
  --issuer-dir "$HOME/elevhub-udsteder" \
  --san localhost --san 127.0.0.1
python3 server/elevhub/opret_sikkerhed.py add-device \
  --output-dir "$HOME/elevhub-sikkerhed" --name <ELEV-VM>
```

Eksemplet er kun til en loopback-pilot. Tilføj pilotens faktiske servernavn/IP til `init`, før en anden computer skal nå serveren. Den eksisterende mappe kan ikke genbruges med `init`. CA-udstederens private nøgle opbevares separat og skal ikke på runtime-serveren eller elevcomputerne.

### 2. Importér runtimefiler til Windows

`server/sikkerhed/importer-server.ps1` opretter en ny mappe, fjerner arvede adgangsregler og giver kun den aktuelle Windows-konto og SYSTEM adgang. Scriptet kopierer præcis `server.crt`, `server.key`, `ca.crt` og `enheder.json`. CA-udstedernøglen og klienternes tokens følger ikke med.

Eksempel fra arbejdsområdets rod i PowerShell; tilpas Linux-brugernavnet og vælg et nyt mål:

```powershell
.\linux-elevhub\server\sikkerhed\importer-server.ps1 `
  -Kilde '\\wsl.localhost\Debian\home\elevhub\elevhub-sikkerhed' `
  -Maal '<LOKAL-TESTMAPPE>\sikkerhed-pilot'

.\.venv\Scripts\python.exe .\linux-elevhub\server\elevhub\opret_serverkonfiguration.py `
  --sikkerhed '<LOKAL-TESTMAPPE>\sikkerhed-pilot' `
  --output '<LOKAL-TESTMAPPE>\elevhub-https-pilot.json'
```

Konfigurationsværktøjet validerer materialet og opretter en ny fil uden at starte tjenester. Begge lytteadresser er loopback som standard. Vælg `--elev-adresse` eksplicit, når elevcomputeren skal kunne nå serveren. Brug separat testdata og ledige porte ved samtidig drift med det eksisterende testmiljø.

Windows-browseren skal have tillid til den korrekte offentlige `ca.crt`. Installer kun den CA, der er skabt til makerspacet, efter kontrol af dens fingerprint. Scriptet ændrer ikke Windows' certifikatlager. En egentlig Windows-tjeneste skal køre under en valgt driftskonto med adgang til runtimefilerne; import til din egen konto er beregnet til den lokale pilot.

### 3. Konfigurér elevens Ansible-inventory

Et sikkert inventory skal blandt andet indeholde:

```yaml
kiosk_server: SERVERENS_FAKTISKE_NAVN_ELLER_IP
kiosk_protokol: https
kiosk_usikker_test: false
kiosk_ca_fil: "{{ lookup('env', 'HOME') }}/elevhub-sikkerhed/ca.crt"
kiosk_enhedsfil: "{{ lookup('env', 'HOME') }}/elevhub-sikkerhed/enheder/{{ inventory_hostname }}.json"
# Sæt eventuelt også grundsystem_admin_kilder til de faktiske administrationsadresser.
```

CA'en installeres i systemets certifikatlager og elevens Chromium NSS-database. Root-tokenet kommer kun i `/etc/elevhub/enhed.json`; UI-tokenet er med vilje læsbart for elevkontoen. Del aldrig provisioneringsmappen, Ansible Vault-hemmeligheder eller private SSH-nøgler med elevkontoen.

Kør først Ansible med `--syntax-check` og `--check` mod den valgte testcomputer. En rigtig udrulning kan genstarte kiosksessionen og skal ske, når elevens arbejde er gemt. Den eksisterende test-inventory bruger eksplicit `kiosk_usikker_test: true` og `http`, så den ikke stiltiende migrerer den aktive kiosk.

### 4. Rotation og spærring

Se [provisioneringsvejledningen](server/sikkerhed/README.md) for `add-device` og `rotate-device --confirm`. Når Windows bruger en importeret kopi, skal det opdaterede hashregister også installeres atomisk i Windows' private runtime-mappe; genlæsning på serveren ser kun den fil, serverkonfigurationen peger på. Klientens nye fil skal distribueres via den beskyttede administrationskanal. Planlæg kortvarig lukket adgang, mens begge sider opdateres.

For at spærre en computer fjernes dens registrering fra den aktive `enheder.json` med atomisk filudskiftning. Den gamle status udløber efter den normale frist. Ingen certifikater eller enhedsnøgler udskiftes af almindelig serverstart.

## Det, der kræver det rigtige miljø

- Router/VLAN, firewall, Wi-Fi, internet-uplink og konkrete tilladte forbindelser mellem elev-, lærer- og maskinområdet.
- Printer-/lasermodeller og afprøvning af lokal maskinstyring og eventuelle cloudkrav.
- Valg og afprøvning af backupmål, kapacitet, adgang og gendannelse efter fejl på en elevcomputer.
- Kontrol af browserens faktiske certifikattillid, kioskgenveje, USB-opstart og fysisk adgang på elevudstyret.
- Endelig driftskonto, beskyttelse af SSH-nøglen, Windows-tjenester og planlagte genstarter efter opdateringer.

TLS og enhedsidentitet beviser ikke, at en kompromitteret root-konto taler sandt, og de må ikke erstatte netværksafgrænsning. UI-oplysninger om aktivt værksted er elevsidens oplysninger; kvitteringen for anvendt politik kommer særskilt fra root-tjenesten. Python-serveren er fortsat en lokal projektserver, selvom adgangen og ressourceforbruget er strammet. Den er ikke beregnet til direkte eksponering på internettet.

## Verifikation

Kør relevante server- og klientprøver i det operativsystem, som installationen
bruger. Linux-rettigheder, browserens certifikattillid, USB, offline opstart
og genforbindelse skal prøves på det konkrete udstyr. Kildeprøver erstatter
ikke skolens driftsprøve.

## Officielt grundlag

- [Python: TLS og certifikatverifikation](https://docs.python.org/3/library/ssl.html).
- [OpenSSH: sshd_config](https://man.openbsd.org/sshd_config).
- [OWASP: Transport Layer Security](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html).
- [Restic-dokumentation](https://restic.readthedocs.io/).
