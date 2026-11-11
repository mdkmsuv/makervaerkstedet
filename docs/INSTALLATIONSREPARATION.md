# Genoptagelse af en afbrudt Windows-førsteinstallation

**Kreditering: Klostermarksskolen.** Denne vejledning gælder genoptagelse af
en afbrudt førsteinstallation med samme verificerede pakke og skolevalg.
Genoptagelse er en særskilt handling og er ikke en versionsopdatering.

## Understøttet genoptagelse

Brug samme verificerede pakke og samme skolevalg som ved det første forsøg.
Start skoleopsætningen med `-Resume`, eksempelvis:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\school-setup\Setup-School.ps1 -Resume
```

Genoptagelse er ikke en opdatering af en færdig installation til en ny pakke.
Pakkens identitet, ejerskab, stier, netværk, porte og layout skal stemme.
En allerede installeret server geninstalleres ikke af skoleopsætningen.
Skoleopsætningen videresender `-Resume` for en ejerbundet førsteinstallation
i tilstanden `installing`, `rollbackFailed` eller `rolledBack`. Før oprydning
eller genforsøg kontrolleres den oprindelige pakke og release, ejer-id,
netværk, TLS-materiale, konfiguration, programfiler og Windows-ressourcer.
En delvist kopieret programfil accepteres kun, hvis dens bytes er en præcis
begyndelse af den oprindelige pakkefil. Ukendte eller ændrede filer afvises.
Generationslayoutet kan her kun repareres ved revision 1 og oprindelig datarod.

## Certifikater og nøgler ved en afbrydelse

En ny førsteinstallation gemmer hele sit oprindelige TLS-materiale i en
ejerbundet journal, før CA, servercertifikat, servernøgle eller enhedsregister
skrives til deres endelige filer. Materialet kan dermed færdiggøres med
præcis de samme bytes efter en afbrydelse mellem filskrivningerne.

Journalen hedder `security/tls-initialization.json`. Under klargøring
indeholder den privat nøglemateriale og ligger under installationens private
datamappe. Efter fuldført oprettelse erstattes den atomisk af en kvittering
med identitet og kontrolsummer, uden en ekstra kopi af den private nøgle.
Journalen indgår i den almindelige databackup.

Genoptagelse udfylder kun manglende filer, som den gyldige journal beskriver.
Allerede udgivne filer skal stemme. Den kontrollerer også, at nøgle og
certifikat passer sammen, at certifikatet hører til CA'en, og at serveradressen
passer. Efter fuldført oprettelse må enhedsregisteret naturligvis have fået
flere elevtilmeldinger; de bevares.

Der er lokal testdækning for fejl før og efter de atomiske filskrivninger,
fuldstændige og afbrudte midlertidige journalfiler, forkert ejer/serveradresse,
ændrede certifikatfiler og gentagne genforsøg. Testene opretter egne nøgler
og midlertidige mapper og ændrer ikke computerens aktive certifikattillid.
Fire yderligere Windows-tests stopper en rigtig provisioneringsproces efter
journal, CA, servernøgle eller afsluttende kvittering. De kontrollerer den
ejede proces' faktiske PID og afslutning og genoptager derefter med uændrede
oprindelige nøgler og en bevaret køfil.

## Tilstande, som ikke må repareres ved at slette data

- Et hårdt processtop kan efterlade ejerskabsmarkøren i `installing`.
  `-Resume` kan nu rydde op i dokumenteret ejede rester og genoptage. En
  ødelagt eller ugenkendelig ejer-/kontrolfil afvises; et faktisk strømsvigt
  på en installeret Windows-server er endnu ikke afprøvet.
- Hvis forsøget stoppede, før den første ejerskabsmarkør blev skrevet, kan
  værktøjet ikke fastslå ejerskabet og overtager ikke de tilbageværende filer.
- En gammel, ufuldstændig TLS-mappe uden en gyldig journal kan ikke
  rekonstrueres sikkert. Manglende privat nøgle eller en ændret identitet
  kræver undersøgelse og relevant backup; værktøjet opretter ikke en ny
  identitet oven på resterne.
- `rollbackFailed` kan genoptages, når ejerskab og resterne kan valideres.
  Eksternt ændrede opgaver/firewallregler eller optagne driftslåse afvises
  fortsat. Genforsøget overtager ikke sådanne ressourcer.
- En anden pakke, et andet netværk eller et andet layout afvises før ændringer.
- Et eksisterende skole-/serverlayout, nøgler, kødata og konfigurationsfil
  slettes ikke som normal genforsøgsprocedure.

En reel installation på de valgte Windows- og Windows Server-versioner skal
stadig afprøve ACL'er, opgaver, firewall, genstart og tilgængelighed. Lokale
PowerShell- og kryptografitests erstatter ikke denne installationsprøve.

## Afinstallation og bevarede generationer

`Remove-School.cmd` bruger den installerede fjerner og kontrollerer ejerskab,
opgaver, firewall, CA, genveje, processer, stier og driftslåse før fjernelse.
`Remove-School.ps1 -WhatIf` kan vise den planlagte handling.

Ved en uændret første generation kan de kendte programfiler fjernes, mens
data og nøgler bevares. Efter en opdatering eller gendannelse fjernes kun
den kontrollerede Windows-integration. Alle programgenerationer, den faste
runtime, kontrolkvitteringer, kødata, elevfiler og nøgler bevares som arkiv.
Den oprindelige og den valgte installations ejer markeres `removed`.
Arkivet optager fortsat diskplads, og dets programmer er ikke teknisk
forhindret i at blive startet manuelt. Oprydning i arkivet kræver særskilt
kontrol af data, nøgler, aktive processer og referencer.

Genopbygning af en helt tabt Windows-vært fra backup er fortsat et åbent
releasekrav. Dette førsteinstallationsforløb erstatter ikke den funktion.

## Forny servercertifikat uden at ændre skolens tillid

På en ny installation med `generation-v1` og bevaret `security/ca.key`
bruges **menu 12 i den installerede `Manage-School.cmd`**. Kontrolpanelet
viser server- og CA-certifikatets udløbsdato og advarer ved 30 dage.
Fornyelsen kræver administratoradgang og en gyldig CA; den er derfor en
planlagt driftsoperation. Gem arbejdet før serverens midlertidige stop.

Operatoren finder den aktuelle datageneration og kontrollerer ejerskab og
Windows-opgaver. Den gemmer deres tidligere tilstand, stopper dem og holder
driftslåse under fornyelsen. Kun `server.crt` udskiftes: CA, servernøgle,
serveradresse/localhost, elevtilmeldinger, SSH og printerdata bevares.
Den nye gyldighedsperiode går aldrig ud over CA'ens gyldighed.
Opgaverne returneres til deres tidligere tilstand efter en vellykket handling.

Ved fejl eller lukket konsol vælges menu 12 igen. Den samme varige operation
genoptages med samme certifikat og kvittering. Undlad at redigere eller
slette TLS-journalerne, genaktivere opgaver manuelt eller begynde en anden
opdatering under et afbrudt forløb. Ved et efterfølgende menuvalg efter
afslutning oprettes en ny fornyelse. Der er ikke hot-swap af certifikatet
i en kørende webtjeneste.

CA-signaturnøglen, private TLS-journaler og kvitteringer er kun læsbare
for Administratorer og SYSTEM. De skal med i en beskyttet skolebackup,
men aldrig i en offentlig pakke eller USB-forbindelsesfil til eleverne.

En ældre installation uden CA-signaturnøglen afvises uden dataændring.
Nøglen kan ikke genskabes ud fra `ca.crt`. En udløbet CA kan heller ikke
reddes ved at forny dens leaf; den situation kræver en særskilt plan for
ændring af skolens tillid på enhederne. Menuen erstatter ikke CA'en
automatisk. 

## Serveren starter, før netværksadressen er klar

Windows kan starte serverens opgave, før den valgte IPv4-adresse er bundet
til et netkort. Hvis loggen viser WinError 10049, kontrolleres den valgte
adresse mod installationens `installation-owner.json` og
`konfiguration.json`; ret ikke adressen ud fra en midlertidig DHCP-værdi.
Serveren venter nu, indtil netop den konfigurerede adresse kan bindes lokalt,
før den starter sine tjenester. En stopanmodning afbryder ventetiden. Andre
socketfejl bliver rapporteret med det samme og behandles ikke som opstartsventen.

Denne kontrol bekræfter kun, at adressen findes lokalt. Den kontrollerer ikke
DNS, internetadgang, Windows IP Helper, portproxy eller klientadgang. Hvis
serverens lokale tjenester svarer, men andre computere ikke kan forbinde,
undersøges de relevante netværkstjenester og den snævre forwarding-regel
særskilt; åbn ikke en bred firewallregel som genvej.
