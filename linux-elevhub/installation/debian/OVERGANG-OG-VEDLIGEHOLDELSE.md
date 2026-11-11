# Elevgrundsystem: overgang og vedligeholdelse

Normal programopdatering bruger skolens signerede apt-kanal. Den kræver ingen ny
ISO. En ISO er kun relevant ved en valgt ren grundinstallation eller et
OS-grundlag, som den understøttede overgang ikke accepterer. En eksisterende
programversion er ikke dokumentation for operativsystem eller korrekt bootstrap.

## Den understøttede eksisterende installation

`bootstrap_existing.py` er en særskilt overgang, ikke clean-installerens --apply.
Den accepterer kun Debian13/amd64 med eksisterende gyldig root-enrollment,
skole-CA og moderne HTTPS/UI-konfiguration. Den eksisterende elevkonto/session
bevares. Der skal være præcis én kendt startx-binding og én kendt tty1-dropin.
En kørende gammel eller ny opdateringsproces blokerer. Andre OS-generationer,
ændrede profiler og ukendte bindinger kræver ny vurdering; afvisningen skal ikke
omgås med ændrede OS-markører eller bred oprydning.

Forberedelse kræver frisk OS/dpkg/service/session-inventar af en faktisk online
elev. Kildeprøver på syntetiske træer godkender ikke en bestemt skolecomputer.
Før drift skal overgangen gennemføres på en reserveelev fra den konkrete gamle
generation og kontrolleres efter genstart og offline koldstart.

Overgangsmediet skal være en gennemgået, publisher-signeret bootstrapleverance med
scriptet og dets hashbundne apt_sandbox.py ved siden af, det præcise firepakkesæt og det signerede lokale apt-repository. Kontrollér
leverancens detached minisign-signatur med den godkendte offentlige nøgle og den
pinnede minisign0.12, før dens programmer køres. `install-lock.json` binder filer
med hashes, men er ikke i sig selv en publishersignatur. Kopiér den verificerede
leverance til en ny rootbeskyttet stagingmappe; elevskrivbare mapper og links må
ikke bruges som installationsinput. Private signeringsnøgler indgår aldrig.

Som root under en særskilt aftalt vedligeholdelse:

```sh
python3 bootstrap_existing.py --payload /root/VERIFICERET/payload > plan.json
sha256sum plan.json
```

Første kommando er kun inspektion. Planen binder også install-lock.json med SHA256 og kræver metadata/poolfiler til alle fire pakker. Den indeholder hashes og status, ikke
root-tokenets indhold. Gennemgå den konkrete identitet, session og de gamle
bindinger. Gem den gennemgåede plans SHA256. Først en særskilt autoriseret
installation bruger:

```sh
python3 bootstrap_existing.py --apply --payload /root/VERIFICERET/payload \
  --plan /root/VERIFICERET/plan.json --plan-sha256 DEN_GENNEMGAAEDE_SHA256
```

Scriptet genkontrollerer samme inventar og payload før første ændring. Før APT-preflight tages den samme eksklusive klientlås som normal updater; aktiv updater kontrolleres igen før session eller gamle bindinger ændres. Kun loadede aktive units stoppes. Det
stopper elevsession/lokale tjenester i vedligeholdelsen. Kun de navngivne gamle
`elevhub-full-update.service`, `elevhub-firstboot.service`,
`elevhub-onboarding.service` og `getty@tty1.service.d/zz-full-update.conf`
arkiveres, hvis de findes; de tre gamle tjenester maskeres. Den præcise installerede cadoodle1.9.6/status bindes i planen. Før kioskændring kræves dpkg --no-act --remove cadoodle; efter ny pakkeinstallation fjernes kun denne kendte pakke med dpkg --remove cadoodle. Reverse-dependencyproblemer afvises uden force. Der køres ingen
purge/autoremove eller sletning af elevprodukter, køer eller historik. En maskine uden pakken fjerner intet; andre installerede CaDoodle-versioner kræver manuel vurdering. Andre gamle
opdaterings-/bootbindinger skal afklares i piloten og føjes til en ny konkret
reviewet understøttelse, før en maskine med dem accepteres.

Før nogen service-/session-/programændring kontrollerer native apt repositorysignaturerne og simulerer de fire eksakte pakker mod en kopi af den aktuelle dpkg-status. Preflight bruger kun det signerede lokale repository, en hookfri konfiguration og egne midlertidige lister/cache/logs. Installation bruger samme kilde og validerede indeks samt virkelig dpkg-status, uden removal. Der er bevidst ingen automatisk fuld systemkilde-refresh eller internetfallback. Manglende OS-afhængigheder stopper sikkert før kiosken ændres og håndteres som en særskilt autoriseret systemvedligeholdelse før nyt forsøg. Gyldig enrollment/CA
bruges til den kanoniske project-source og klientkonfiguration; der oprettes
hverken en ny identitet eller en skjult driftskonto. Ny firstboot, lokale
tjenester og timer aktiveres. Timeren startes først efter målt runtime-health.
Skoleserveren skal også have enheden bundet til student/debian-amd64 og en
ønsket udgivelse. Almindelige apt-opdateringer overtager derefter.

Forsøget og de præcise tidligere filer ligger rootbeskyttet under
`/var/lib/makervaerkstedet-updater/bootstrap`. `complete.json` kræver korrekt
dpkg-inventar, aktiveret program og målt runtime. `failed.json` skrives kun efter en faktisk vedligeholdelsesændring og kræver manuel
vurdering; en låsekonflikt eller fejlet preflight stopper uden failed-markør og uden at ændre kiosk/bindinger. samme script foretager ikke en blind geninstallation. En afbrudt
bootstrap kan efterlade elevtjenester stoppet eller gamle bindinger maskeret.
Gennemgå journal, dpkg og de arkiverede filer før en konkret reparation eller
returplan. Der er ingen automatisk OS-/bootstraprollback.

## Hvad en normal programopdatering kvitterer for

Efter apt bruges den nyinstallerede renderer. Systemd genindlæses, og de faste
lokale tjenester/tty1 genstartes, hvis de kører. Derefter kræves aktive lokale
services og tty1 samt svar fra projekter, Scratch, micro:bit, laser og folie på
loopback. Svarene skal have korrekt service, faktisk systemd-MainPID, en gyldig
samme-process UUID og den forventede coreversion. Inaktiv tjeneste, gammel
runtimeidentitet eller manglende svar giver fejl og ingen succesrapport.

Corepakken indeholder den hashbundne fælles helper og en descriptor med dens
egen Debianpakkeversion. Identiteten indlæses én gang ved processtart. En ændret
descriptor kan derfor ikke få en gammel proces til at fremstå opdateret.
Updaterens første health-udgave kræver core>=2.0.0~20261109.2.

De fire komponenter har selvstændige versioner. Runtimekontrollen sammenligner
med den faktisk installerede coreversion, som ved coreopdatering skal svare til
det verificerede coremanifest. En updater-only-, Scratch- eller Matchboxudgave
kræver ikke, at uændret core får feedets nye samlede tag. Ingen egne releaseudløb
indføres; publishersignatur, hashes, sekvens/forkkontrol, eksplicit rollback og
Debians metadataudløb bevares. En failed aktivering kræver vurdering; dpkg-version
alene er ikke en vellykket opdatering. Hardware/grafisk betjening prøves særskilt.

## Enkel Debian-sikkerhedsrutine

Aftal ét tilbagevendende vedligeholdelsestidspunkt, eksempelvis fredag kl.15.00,
hvor læreren først afslutter elevbrug. Dette er en operatørrutine, ingen ny
managementplatform eller automatisk genstart. Skolens programtimer og
OS-vedligeholdelsen må ikke køre apt samtidigt; stop programtimeren under det
særskilt autoriserede forløb og kontrollér, at ingen opdatering kører.

Brug de eksisterende Debian-kilder og normale signatur-, TLS- og Valid-Until-
kontroller. Gennemgå først planlagt opdatering; installér almindelige
sikkerheds-/systempakker uden removal. En distro-opgradering eller plan med
pakkeremoval er en anden beslutning og hører ikke til denne rutine.

```sh
apt-get -o Acquire::Check-Valid-Until=true -o Acquire::Check-Date=true update
apt-get --simulate --no-remove --with-new-pkgs upgrade
# Kun efter gennemgang og installationstilladelse:
apt-get --no-remove --with-new-pkgs upgrade
python3 /usr/lib/makervaerkstedet/updater/os_maintenance.py
```

Statusværktøjet er rent læsende. Det respekterer `/run/reboot-required` og
sammenligner valgt `/vmlinuz` med den kørende kernel. `reboot_required:null`
betyder ukendt valg, ikke godkendt uden genstart. Der er ingen automatisk reboot.
Gennemfør en nødvendig, særskilt aftalt genstart i vinduet; kontrollér efter boot
lokal session, offline workshops og runtime-health. Start programtimeren igen,
når OS-forløbet og health er godkendt. Hvis reparation eller reboot udestår,
registreres det tydeligt som udestående vedligeholdelse.
