Makerværkstedet: opdatering af skolens eksisterende flade Windows-installation
Kreditering: Klostermarksskolen.

Dette er en afgrænset opdatering af eksisterende ElevhubServer + separat
Printvaerksted på skolens Windows-server. Det er ikke ren installation,
genoptagelse, backupgendannelse eller generation-v1-opdatering. Der bygges
ingen Windows-elevklient. Pakkeversioner fra tidligere udgivelser bevares som
baseline; den valgte kildeopdatering attesteres særskilt i school-update.json
og candidate.json med SHA256.

Staging skal indeholde candidate/ samt disse tre søsterfiler:
  update.py
  Invoke-SchoolUpdate.ps1
  Repair-SchoolRuntimeAccess.ps1

Kandidaten bygges med update.py build fra aktuelle app/web/hub/server-kilder
og runtime fra den eksplicit valgte, verificerede grundpakke. Alle runtimefiler verificeres
mod pakkens integrity.json. Installeret python.exe/python314.dll/python314.zip
skal have præcis samme hashes; dette forløb udskifter ikke interpreterens ABI.

Windows skal have long-path support aktiveret for Python-runtime, så komplette
backupstier over MAX_PATH kan kopieres. Før kørslen opretter backup eller
stopper opgaver, kontrollerer den LongPathsEnabled som REG_DWORD 1 under
HKLM\SYSTEM\CurrentControlSet\Control\FileSystem. Hvis værdien mangler eller
er slået fra, blokeres opdateringen uden at ændre opgaver eller installerede
filer. Aktiver indstillingen gennem skolens Windows-politik:
Computer Configuration > Administrative Templates > System > Filesystem >
Enable Win32 long paths. En administrator kan også sætte den lokalt i en
forhøjet PowerShell:
  New-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
    -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
Brug ikke en lokal registreringsændring, hvis Group Policy eller Intune styrer
denne indstilling. Windows kræver både maskinindstillingen og en longPathAware
programmanifest; den valgte CPython-runtime har denne markering.

Kør først validering uden -Apply. Den konkrete opdatering køres som SYSTEM
eller administrator med en ny journalsti, verificeret candidate.json-hash og
processpecifik ExecutionPolicy Bypass efter behov. Brug aldrig ren installation
eller -Resume som genvej. Eksisterende journaler overskrives ikke.

Forløbet gemmer alle ejede Windows-opgavers XML, principal/actions og aktive/
aktiverede tilstande; stopper deres processer kontrolleret; afviser aktive
hardwarejob; holder tjenesternes datalåse; sikkerhedskopierer SQLite inkl. WAL;
afprøver migration på kopi og kræver bevarelse af gamle rækker, kolonneværdier
og eksisterende datafiler. Begge installerede integrity.json-formater bevares:
Printvaerkstedets historiske flade map og ElevhubServerens {algorithm,files}.

Før genstart sikrer den snævre adgangshjælper, at LocalService kan åbne den
nye rod-instance.lock, og at den nye serverprotokol findes med læseadgang.
Rodens, konfigurationens og sikkerhedsmappernes adgang udvides ikke. Alle fem
anvendte tjenestelåse kontrolleres. Interne bridge-nøgler har kun LocalService
læseadgang og administrator/SYSTEM fuld adgang. Eksisterende konfiguration,
identiteter, nøgler, printeropsætning, køer og historik bevares.

Printpausen gemmes før opdateringen, og køen holdes midlertidigt på pause ved
opstart. Gendan kun det tidligere valg gennem den autentificerede køfunktion
efter kontrolleret helbred/bevaring. En pauseændring frigiver ikke en plade.
Fysisk rydning, printstart og produktion er selvstændige handlinger.

Fejl før genstart ruller program og data tilbage, mens tjenesterne er stoppet.
Fejl efter genstart gendanner aldrig automatisk databaser: nye afleveringer
skal bevares. Undersøg journal/logs og reparer det konkrete problem. Python
bevarer privat failure-traceback.txt før rollback; fejl ved rollback gemmes
separat og efterlader opgaver deaktiveret. PowerShell læser hele stderr og
exitstatus, før en native procesfejl rapporteres.

Målrettede kildetests:
  python -m unittest scripts.school_server_update.test_update -v
