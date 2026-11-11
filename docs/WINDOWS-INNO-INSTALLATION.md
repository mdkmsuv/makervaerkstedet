# Windows: installation og opdatering

Der er to faste EXE-roller: serveren kræver administrator/UAC, og den separate
laserstation køres som den aktuelle bruger. Begge bruger samme Inno-opskrift.
Installerens programnavn er Makerværkstedet; filnavne kan fortsat være uden æøå.

## Kontrollér download før start

EXE-filen får en separat minisign-signatur (`.exe.minisig`). Den har ikke et
Authenticode-certifikat. Windows kan derfor vise en advarsel om en ukendt
udgiver, selv om minisign-kontrollen lykkes. Den vedhæftede minisign-signatur
fjerner ikke Windowsadvarslen. Stop ved forkert signatur eller uventet filhash.

Brug en allerede betroet minisign-verifier og denne på forhånd kendte offentlige
projektnøgle. En nøgle, der kun følger med et ukendt download, etablerer ikke
tillid i sig selv. Verificér den præcise rollefil og dens tilhørende signatur:

```powershell
minisign.exe -V -m Makervaerkstedet-server-Setup.exe -x Makervaerkstedet-server-Setup.exe.minisig -P RWSQY/AmP4l+UEfOaUyois4oJcf8wgj3XpLwpvUWEmrzPn02LJ7+ODDa
```

For laserstationen bruges dens eget EXE-filnavn og signatur. Start kun den
verificerede fil; der er ingen instruktion om at slå Windowsbeskyttelse fra.

## Ny installation

Guiden registrerer automatisk, om en kendt installation findes. Ved ny server
angives skolens navn, serverens IPv4-adresse og elevnettet som CIDR. Derefter
vælges en af de eksisterende roller: server med separat laserstation (`vm`)
eller fælles makerspace-computer (`makerspace`). Vælg også rød, gul eller grøn
og skolens Beambox II eller HEXA. Ingen skoleidentitet eller adgangsnøgle følger
med den generiske pakke. Den eksisterende lokale skoleopsætning opretter dem.

En separat laserstation bruger forbindelsesfilen fra skolens server. Ekstern
Beam Studio og fysiske job er uden for installerens ændringer.

## Opdatering

Download og verificér den nye EXE for samme rolle, og kør den igen. Du skal ikke
vælge mellem installation og opdatering. Kendte standardrødder og installerens
ejerkvittering bestemmer handlingen. Ukendte, ufuldstændige eller ændrede rødder
afvises med en dansk fejl før programudskiftning. Et generationslayout kræver
den eksisterende administrationsvej; det konverteres ikke automatisk.

Opdateringen bevarer skoleprofil, identitet, maskinopsætning, projekter, køer og
historik. Serverens printer- og laseradmission pauses før den ejede opgave
stoppes. Ingen fysisk stopkommando sendes, og pausen bliver stående efter
opdateringen. Den ansvarlige kontrollerer den faktiske maskintilstand, før køen
åbnes igen. Der findes ingen Windows-devicepoller, som automatisk anvender
lærerens ønskede version: Windows opdateres med den nye rolle-EXE.

Før programudskiftning tages en konsistent SQLite-backup af hver kendt lokal
database via SQLite-backup-API'et, også når data ligger i WAL. De faste lokale
serverdatabaser og en eventuel simple-update-database kopieres. Eksterne
backenddatabaser kontaktes ikke og er ikke med; kvitteringen angiver dette.
Dette er ikke en samlet backup af projektfiler, nøgler eller eksterne systemer.

Serverens manuelle databasekopier og hashkvittering ligger i
`%ProgramData%\ElevhubServer\installer-updates\<operations-id>\databases-manual`.
Kun administrator/SYSTEM har adgang. De bevares til vurderet manuel
genopretning. Programfejl kan rulle programfiler tilbage; databaserne og
senere indsendelser rulles aldrig automatisk tilbage. Manuel datagenopretning
kræver vurdering af aktuelle job, indsendelser og ændrede rettigheder.

Installeren giver LocalService skriveret til den dedikerede
`public-apt-cache`, ikke til private konfigurations-, security- eller nøglefiler.

## Linux og tillidsgrænsen

Elevcomputere bruger lærerens eksisterende enhedstilknytning og native apt,
ikke Windows-EXE'en. Apt kontrollerer arkivsignatur og TLS; Debians normale
`Check-Valid-Until=true` beholdes. Egne udgivelser har ingen udløbsdato.

En projektsignatur beviser udgiverens autentiske bytes. Den beviser ikke, at en
ældre version er et sikkert eller kompatibelt valg. Kontrolleret rollback til
en præcis ældre autentisk udgivelse er server-/lærerautoriseret. En kompromitteret
autoriserende server kan misbruge den tillid; signaturen beskytter ikke imod
netop denne autoriserede nedgradering. High-water sænkes ikke automatisk.

## Verifikationsgrænse

Kildeprøver, SQLite/WAL-prøver og Windows-DACL-prøver er adskilt fra faktisk
installation. Compilerprøver med et syntetisk payload beviser opskriftens syntaks,
ikke en færdig grundinstallation. Rigtig ren installation og opdatering på en
godkendt målcomputer/VM mangler, indtil der er givet særskilt tilladelse og en
konkret installationskvittering foreligger.
