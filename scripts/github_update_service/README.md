# GitHub-controller på Windows-hovedsystemet

Kreditering: Klostermarksskolen.

Controlleren kører uden AI, fra en separat SYSTEM-opgave og en Python-runtime
uden for de programmer, der skal opdateres. Læreren vælger pakker og computere
i programmets eksisterende opdateringskø. Nøgler, kommandolinjer og lokale
stier kommer udelukkende fra den beskyttede lokale profil.

`Install-Controller.ps1` viser først en plan uden at installere. IT angiver
kilder, en ny controller-mappe, en selvstændig administratorinstalleret Python
3.11+ under Program Files med standardbibliotek
og en lokalt gennemgået profil. `-Apply` beskytter mappen, kopierer kun
controllerens nødvendige filer, kontrollerer kopiernes hashes og opretter
opgaven `Makervaerkstedet-GitHubUpdates`. Den overskriver aldrig en eksisterende
controller eller dens opgave. Driftsinstallation med skolens aktive data er
ikke udført; den isolerede prøve på server-VM'en er beskrevet nedenfor.
Runtime og hele dens
mappe skal være skrivebeskyttet for almindelige brugere; en Python fra en
brugermappe afvises. Opgaven bruger Pythons isolerede tilstand.

Profilen indeholder `schema: 1`, `repository: "ejer/projekt"`, absolut
`data_root` for Elevhub, `staging_root` for feedets downloads og `profiles`.
Installeringsscriptet udfylder `code_root`, `work_root` og `store_root`.
GitHub-lageret er lokalt fastholdt; browserens kildevalg ændrer ikke denne
privilegerede tillid.

Hvert mål har `id`, `name`, `role` og `worker_target`. Brug eksisterende
opdateringsmåls identiske felter ved videreførelse; profilen må ikke stille
ændre deres transport eller identitet.

Før første GitHub-opdatering sammenlignes computerens kendte, installerede
version med den valgte version. Samme eller ældre version geninstalleres
ikke. En ældre installation uden en entydig versionsmarkør kræver, at IT
verificerer den installerede baseline og skriver `baseline_version` i den
beskyttede profil. Dette er en engangsmigration fra de gamle markører;
controlleren gætter aldrig, at en gammel tekstmarkør svarer til en dato.
Efter den første kontrollerede opdatering bruges installationens egne
versionsbeviser. En pakke med ændret indhold skal have en ny version.

| Rolle | Yderligere lokale profilfelter |
| --- | --- |
| `windows_server` | `layout: "school-flat-v1"`. Controlleren kræver eksisterende ElevhubServer + Printværksted, deres markører og Windows-opgaver. Andre layouts afvises. |
| `linux_student` | `ssh_key`, `known_hosts`, `host_key_alias`, `identity_file`, `identity_sha256`, `registry`, `allowed_networks`, eventuelt `identity_name` og `host_key_policy: "enrollment_proof"`. Tilmelding og et frisk HMAC-bevis kontrolleres før payload. |
| `windows_laser_teacher` | `ssh_key`, `known_hosts`, `host_key_alias`, `user`, `hosts`, samt `install_path_policy: "user_default"` eller en eksplicit `install_path`. Kun en eksisterende station opdateres. |

`ssh_key` og `known_hosts` er absolutte filstier. `identity_file` peger på
serverens aktuelle tilmeldingsregister; dens token-digest skal matche.
`registry` peger på det aktuelle online-register. `allowed_networks` er en
eksplicit liste af skolens godkendte CIDR-net. Ingen af disse oplysninger
publiceres i GitHub-pakkerne.

Importerens immutable kvittering går til
`Controller(config_path).provision_import(receipt)`. Kontrollerede pakker
materialiseres med importerens sikre ZIP-kontrol. Linux har `payload.tar.gz`,
laser har `station-code.zip` (eller det eksisterende direkte stationformat),
og Windows-server har `candidate/`. Serverens kandidat og alle rollepakker
skal have samme version som feedet. Lokale configs, katalogposter og mål
bindes til `github-{role}-{version}` og SHA-256. Eksisterende købestillinger
og deres versioner bevares. Uden en lokal profil bliver pakken ikke kaldt klar.

Controlleren genkontrollerer udgiverens aktuelle GitHub-manifest før import.
En pakke fra en tidligere GitHub-release, der endnu ikke var importeret,
venter på IT-afklaring, hvis den ikke længere er den aktuelle release.
Allerede importerede og bestilte versioner fortsætter uden GitHub-forbindelse.
Kataloget og targetfilen ændres kun ved import; en afbrudt publicering kan
gentages. SQLite-køens leases og controllerens proceslås forhindrer parallel
udførelse af samme bestilling.

Linux bruger den eksisterende bevarende boottransaktion og genstarter ikke
eleven. Status er fortsat *afventer genstart*, indtil et autentificeret
efterfølgende probe kontrollerer installerede filhashes, bevarelse, version
og payloadens faktiske rod-ejede `pins.json`. Laseren bruger det eksisterende idle-værn, stationsrollebevis
og rollbackjournal. Controlleren binder svaret til operation og version.

Windows-serveren bruger den eksisterende opdatering af skoleinstallationen.
Den afviser aktive print-/laserjob, sikkerhedskopierer data inkl. SQLite WAL,
afprøver migration og stopper/gendanner kun installationens egne opgaver.
En completion kræver kontrollerede tjenester og faktiske installerede
filhashes. Ved procesnedbrud afklares den gamle journal; der startes ikke
automatisk endnu en installation. Fejl før opstart giver verificeret rollback.
Efter tjenestestart kræver usikker tilstand hjælp fra IT, så nye afleveringer
ikke kan blive overskrevet med en gammel backup. Den tidligere transaktion
bevares ved lærerens eksplicitte genforsøg efter verificeret rollback.
Printkøen står på pause efter serveropdateringen til lærerens gennemgang.

Den native Windows-prøve kontrollerer controllerens rettighedsgrænser og
opgavestart i et isoleret miljø. Den erstatter ikke en offentlig release-
eller installationsprøve med skolens målcomputere og udstyr.

IT kan køre prøven i en forhøjet session på en valgt Windows-prøvemaskine.
Variablerne skal pege på gennemgåede, beskyttede kildefiler, en selvstændig
Python-runtime, en evidensmappe uden for fixture-mappen og et nyt ID med
12 små hextegn:

```powershell
.\scripts\github_update_service\test-native-controller.ps1 `
  -SourceRoot $ReviewedSource -Python $QaPython -EvidenceRoot $Evidence `
  -RunId $RunId -Mode Run
```

Prøven afviser en eksisterende controlleropgave eller fixture og bruger en
tom lokal profil uden feed eller opdateringsmål. Gennemgå og bevar evidensen,
før samme kommando køres med `-Mode Cleanup`; oprydningen fjerner kun
fixture-ejede opgaver, konto og datamappe. De leverede kildefiler, Python og
evidens bevares. Denne prøve dokumenterer Windows-installationens
rettighedsgrænser og opgavestart. Den udfører ingen GitHub-netimport,
SSH-udrulning, faktisk serverrolleopdatering eller printer-/laserjob.
