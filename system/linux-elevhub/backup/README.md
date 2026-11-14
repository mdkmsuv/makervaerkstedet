# Projektbackup med restic

Rollen `ansible/roles/projektbackup` laver versionsbackup af
`/home/elev/Projekter` som root med [restic](https://restic.readthedocs.io/).
Rollen er opt-in: `projektbackup_aktiveret` er `false`, så en eksisterende
installation ikke får en ny tjeneste eller skriver til et ukendt mål.

## Elever må slette; læreren kan gendanne

Brugerens valgte retning er, at elever fortsat må ændre og slette filer i
`Projekter`. Når backup er aktiveret og lykkes, kan læreren gendanne en tidligere
version til en separat mappe. Elevkontoen kan ikke slette backuphistorikken.
Gendannelse sker foreløbig gennem administrationen med restic; der er endnu
ikke en gendan-knap i lærerpanelet.

Backup er periodisk, ikke en optagelse af hver eneste gemning. Standardplanen
er cirka fem minutter efter opstart og derefter cirka hvert kvarter, med op
til ét minuts tilfældig forsinkelse. En fil, der både oprettes og slettes før
en vellykket backup, kan ikke gendannes herfra. Ved slukket computer, manglende
backupmål eller fejl kan den nyeste kopi være ældre. Ikke-gemte ændringer og
filer gemt direkte på USB er ikke omfattet af backup af `Projekter`.

Backup er endnu ikke aktiveret i testmiljøet. Valg af mål og en afprøvet
gendannelse på den rigtige installation er forudsætningen for at bruge denne
arbejdsgang i undervisningen.

## Før aktivering

Vælg først et konkret mål. Et eksternt mål skal være monteret på et fast
mountpoint, for eksempel `/mnt/projektbackup`. Rollen kontrollerer
`mountpoint` før både initialisering og hver backup. Hvis disken mangler,
fejler den uden at oprette eller bruge en lokal mappe med samme navn.

Et lokalt mål kan vælges eksplicit med `projektbackup_maaltype: local`, men det
ligger da på samme systemdisk. Det hjælper ved en fejl eller sletning af
projektfiler, men beskytter ikke mod fejl på den disk, hvor både projekter og
backup ligger. Et eksternt mål er heller ikke en komplet backupstrategi, hvis
det opbevares ved siden af elevcomputeren.

Rollen forventer, at passwordet sættes fra Ansible Vault eller en tilsvarende
secret-kilde. Det skrives første gang til `/etc/projektbackup/restic-password`
med mode `0600`, og både repository, passwordfil, script og systemd-enheder
ejes af root. En eksisterende passwordfil overskrives ikke automatisk;
passwordrotation kræver et særskilt, planlagt valg af
`projektbackup_password_overwrite`. Flaget ændrer kun passwordfilen; selve
repositoryets password skal først være ændret med restics nøglehåndtering.
Gem en kopi af restic-passwordet i skolens
lærer-/administratorstyrede secret- eller offline-backup. Uden passwordet kan
snapshotdata ikke restores, men passwordet skal aldrig være tilgængeligt for
elevkontoen.

## Integration

Rollen er med i `ansible/site.yml`, men udfører ingen installation, før den
aktiveres med et valgt mål og password. Eksempel på inventory-
værdier (passwordet skal være vault-krypteret i den rigtige fil):

```yaml
projektbackup_aktiveret: true
projektbackup_maaltype: mounted
projektbackup_mountpoint: /mnt/projektbackup
projektbackup_repository: /mnt/projektbackup/restic
projektbackup_restic_password: "{{ vault_projektbackup_restic_password }}"
```

For et bevidst lokalt mål bruges i stedet for eksempel:

```yaml
projektbackup_aktiveret: true
projektbackup_maaltype: local
projektbackup_mountpoint: ""
projektbackup_repository: /var/backups/elev-projekter-restic
projektbackup_restic_password: "{{ vault_projektbackup_restic_password }}"
```

Rollen installerer `restic`, initialiserer repository én gang, installerer et
root-ejet backupscript og aktiverer `projektbackup.timer`. Timeren starter
cirka fem minutter efter opstart og derefter cirka hvert kvarter, mens
computeren er tændt. En daglig persistent timer samler også en overset kørsel
op. Der er op til ét minuts tilfældig forsinkelse. Der er ingen automatisk
oprydning, `forget` eller `prune`; versionshistorikken slettes ikke af rollen.
Backupscriptet kontrollerer også, at repositoryets `config` findes, så en
fejlkonfigureret eller tom lokal sti ikke bliver fyldt med backupdata.

## Sikker manuel restore

Restore skal altid ske til en ny, separat mappe, som root ejer. Det beskytter
de lokale projektfiler mod overskrivning, mens læreren inspicerer indholdet.
Kør på elevcomputeren som root (tilpas `REPO` til det valgte mål):

```sh
REPO=/mnt/projektbackup/restic
PW=/etc/projektbackup/restic-password

sudo restic --repo "$REPO" --password-file "$PW" snapshots
RESTORE_DIR=$(sudo mktemp -d /root/projektbackup-restore.XXXXXX)
sudo chmod 0700 "$RESTORE_DIR"
sudo restic --repo "$REPO" --password-file "$PW" restore latest \
  --target "$RESTORE_DIR"
```

`mktemp` giver en ny root-privat mappe ved hver restore, så en recovery ikke
overskriver en tidligere recovery. Brug et snapshot-id fra `snapshots` i stedet
for `latest`, hvis en ældre version skal hentes. Gennemse `$RESTORE_DIR` og kopier
kun udvalgte filer manuelt til elevens projektmappe efter kontrol. Brug aldrig
`/home/elev/Projekter` som `--target`, og kør ikke restore som elevkontoen.

Før restore fra et eksternt mål skal disken være monteret på det samme
mountpoint. En restore fra en lokal mappe på den samme disk hjælper ved
sletning, men kan ikke genskabe data efter diskfejl, tyveri eller korruption
af hele disken; det kræver en separat kopi eller et andet backupmål.
