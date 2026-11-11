# Enkel signeret releasekontrakt, schema1

Agent4 ejer `app/simple_release.py`. Agent2 ejer eksisterende lærer/root-auth,
valg, audit, opdateringsstatus og beskyttet vedvarende high-water. Agent3 ejer
apt/dpkg og Linuxinstallation. Ingen TUF, leases eller egen kryptografi i denne vej.

`verify_manifest(raw, signature, *, minisign_exe, minisign_sha256, public_key,
repository, role, platform, component, now) -> dict` verificerer originalbytes med
pinnet standalone minisign CLI før JSON-parsing. Eksakt verifierkopi køres privat
med faste argumenter, begrænset miljø og timeout. Signering foretages separat.
Exceptions er `ReleaseVerificationError` med stabil `.code`; ukendte fejl må
ikke føre til installation. Source-tests injicerer CLI-resultat; de beviser ikke
kryptografi. Ægte integrationstests i tests/test_simple_release_minisign.py kræver eksplicit MKV_MINISIGN_EXE og MKV_MINISIGN_SHA256; ellers springes de over. Fire sådanne Windowsprøver er bestået med den godkendte0.12-verifier. Syntetiske signingkeys oprettes midlertidigt udenforOneDrive.

Manifestfelter, alle påkrævet: schema=1, repository, role, platform, component,
version, sequence og artifacts. Repository er
mdkmsuv/makervaerkstedet. Roller: student/server/laser_station. Platforme:
debian-amd64 for student, windows-amd64 for server/laser_station. Component er
et eksplicit consumerforventet ID, studentcore, scratch eller matchbox for student; server og laser_station for deres respektive Windowsroller.
Tilladte Debianpakker er fast i DEBIAN_COMPONENTS: studentcore→makervaerkstedet-elev(all), scratch→makervaerkstedet-scratch(all), matchbox→makervaerkstedet-matchbox(amd64), updater→makervaerkstedet-updater(amd64). Updaterpakken ejer agent3s client/timer/activation og pinned Linuxminisign; identitet og private nøgleconfig er ikke payload. Intet signed manifest kan udvide listen til OS-pakker.
Egne udgivelser har ingen udløbsdato; expires_utc er ikke et schemafelt. API-parameteren now beholdes ubrugt af hensyn til callers. Dubletnøgler, ukendte felter og felter med underscore-prefix
på alle niveauer afvises. Returneret dict har beregnet `_manifest_sha256` fra
originalbytes; det felt må aldrig modtages som signeret input.

Hvert artifact har name, package, version, architecture, sha256, size.
Windows: package=null, architecture=amd64, name er .exe. Debian: package har
præcist pakkenavn, architecture=all eller amd64, name er .deb. Artifactversionen skal være præcis manifest.version på Debian, så desiredversionen ikke kan skjule en anden pakkeversion. Versioner bevares
som strenge. Kun Linux bruger dpkg --compare-versions; Windowsserveren må ikke
sortere Debianversioner med PEP440/SemVer. Positive størrelser, SHA256, sikre
unikke basenames og ikke-tom artifactliste kræves.

`verify_artifact(path, artifact)` kontrollerer præcise bytes før brug.
`artifact_url(manifest, artifact)` bygger kun projektets GitHub HTTPS URL:
releases/download/v{manifest.version}/{artifact.name}. Ingen vilkårlig URL eller
skoleidentitet findes i manifestet. Fremtidige officielle komponentdownloads
skal have særskilt fast allowlist; den findes ikke i schema1.

`release_scope(manifest)` returnerer tuple(repository,role,platform,component).
`accept_sequence(manifest, highest_seen_mapping, *, rollback_manifest_sha256=None)`
returnerer max(sequence,highest), som caller gemmer holdbart under denne tuple
før handlinger. En lavere sequence kræver allerede autentificeret, auditeret
lærervalg af præcis `_manifest_sha256`; modulparameteren er ikke auth i sig selv.
Rollback skal fortsat bestå signatur/scope/hashkontrol. High-water sænkes
aldrig. Modulet vurderer ikke pakkernes database-/runtimekompatibilitet.

Signing-private key, offline backup og offentlig nøgles første installation
kræver aftalt ejerskab. Consumer bruger lokalt pinnet offentlig nøgle og
verifierhash. Nøglerotation er ikke implicit tilladt af et downloadet manifest.
Ingen skoledata/nøgler/ProgramData må lægges i generiske pakker.

## GitHub-feed og D4

`app/simple_feed.py` verificerer serverens schema1-feed med præcis disse felter:
schema, repository, release_tag, sequence, snapshot. Snapshot har name, sha256
og size. Feedet har ingen udløbsdato. Samme sekvens med ændrede originalbytes
afvises; lavere sekvens afvises. Feedet giver ikke tilladelse til at sænke
komponenters high-water eller springe lærerens præcise rollbackgodkendelse over.

`build_simple_feed.py` producerer komponentmanifest og feed uden expires_utc.
`build_apt_repository.py` modtager præcis schema=1, packages, test_repository;
byggeinput kræver også install_gate={path,sha256} til den beståede præcise fire-pakke Debian13-simulation. Byggeinput og kvittering har ingen udløbsdato. Signeret Release har ingen
Valid-Until. Behold Acquire::Check-Valid-Until=true, så Debians egne arkiver
fortsat kontrolleres normalt. Historiske daterede prøveartefakter skal genbygges
og signeres på ny før levering; ingen eksisterende signeret fil omskrives.

Tillidsgrænse: en signatur beviser udgiverens autentiske bytes, ikke at en
ældre version er sikker eller kompatibel. Præcis ældre autentisk rollback
autoriseres af server/lærer. En kompromitteret autoriserende server kan misbruge
den tillid; minisign beskytter ikke mod denne serverautoriserede nedgradering.
High-water sænkes ikke. Native apt kontrollerer fortsat egne signaturer og TLS.

SkoleGPT, Kredsløb og Datatræner indgår ikke i aktivt standardkatalog eller installations-/opdateringspayload. Scratch, micro:bit og den generelle Matchbox-vinduesmanager bevares.
