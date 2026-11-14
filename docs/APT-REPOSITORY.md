# Native apt og signeret appkatalog

Linux-elevenheder bruger native Debian-pakker fra skolens server.
Metadata til hovedsystemet og appkataloget har hver sin kontrakt.
En gammel ZIP med formatet `linux-queue-v1` er ikke en apppakke.

## Repository og tillid

Produktionsfilen `/etc/apt/sources.list.d/makervaerkstedet.sources`:

```text
Types: deb
URIs: https://CONFIGURED-SERVER/apt/
Suites: ./
Architectures: amd64
Signed-By: /etc/apt/keyrings/makervaerkstedet.gpg
```

En exact-path suite har intet `Components`-felt. Serverens afgrænsede
`public-apt-cache` indeholder `pool/*.deb`, `Packages`, `Packages.gz`,
`Release`, `InRelease` og `Release.gpg`. Både `all` og `amd64`
indekseres; `dpkg-scanpackages --multiversion` bevarer præcise ældre
versioner. En nedgradering kræver stadig en særskilt autoriseret vej.

Native GPG signerer apt-metadata. Minisign signerer hovedsystemets
manifest, appkatalog og feed. Det er særskilte protokoller og
tillidsnøgler. Brug eksisterende pins og skolens tilmeldte HTTPS/CA.
Brug aldrig `trusted=yes`, usikkert repository, frakoblet TLS-kontrol
eller global `apt-key`-tillid.

Projektets releases har ingen `Valid-Until`; de udløber ikke.
`Acquire::Check-Valid-Until=true` bevares, så Debians normale
udløbs- og signaturkontrol stadig gælder.

## Præcise byggesæt

| Kontrakt | Pakker | Formål |
|---|---|---|
| Schema 1, legacy | Elevkerne, opdaterer, gammel Scratch og Matchbox: præcis fire | Historiske samlede udgivelser |
| Schema 2, `base` | Elevkerne, opdaterer og Matchbox: præcis tre | Tom modulær basis/ISO |
| Schema 2, `app` | Basis plus præcis én `makervaerkstedet-app-<id>` | Selvstændig appudgivelse |
| Schema 2, `migration` | Basis plus de fem indbyggede apppakker: præcis otte | Verificerede inputs til overgang |

Valgfrie apps er ikke permanente afhængigheder af kernen. Et
migrationsbyggesæt tvinger ikke alle fem apps ind på hver computer.
Katalog, native pakkefelter og pakkede descriptors skal matche præcist.

`scripts/build_apt_repository.py` bruger `dpkg-deb`,
`dpkg-scanpackages` og `apt-ftparchive` samt en eksplicit fastlåst GPG.
Den kontrollerer faktiske størrelser, SHA256, pakkeidentitet og
arkitektur før signering. Den opretter ingen nøgle, installerer intet og
publicerer intet. Outputmappen skal være ny og adskilt fra den private
signeringsmappe.

Input skal indeholde `install_gate: {path, sha256}`, som binder den
native `release-build.json`. Schema 2 binder også `release_set` og
`app_descriptors`. Gaten skal have
`all_packages_simulated: true`, `actual_install: false` og præcis de
pakker, versioner, arkitekturer og hashes, der udgives. Et ændret,
manglende eller forkert sæt afvises før signering.
Den fastlåste Debian-arkivnøglering har SHA256
`506b815cbb32d9b6066b4a2aa524071e071761e7e7f68c3ac74f3061ba852017`.

Kvitteringen dokumenterer en reel apt-simulering med tom pakkestatus,
ikke en installation. Den er en lokal integritetsbinding og ikke
uafhængig kryptografisk attestering mod en ondsindet udgiver med
signeringsnøglen. Private kvitteringer og lokale kildestier ligger ikke
i det offentlige repository.

## Appkatalog og enhedens handling

`scripts/build_app_catalog.py` binder appdescriptor, kildecommit,
sekvens og præcist artefakt. `scripts/build_simple_feed.py` validerer
apt-kvitteringen, signerer katalog og feed og fremstiller de faste
GitHub-assets. Serveren verificerer importen og tilbyder kun accepterede
pakker. Samme appversion må ikke senere pege på andre bytes.

Lærerens appordrer går gennem `/api/admin/apps/order`; enhederne bruger
den separate autentificerede `/api/device/apps` med protokollen
`apps-v1`. Native updater kontrollerer signaturer, sekvens, version,
plads, arkiv, ejerskab, port-/formatkonflikter og den simulerede
apt-plan før en ændring. Browseren får ingen vilkårlig apt-, URL- eller
shelladgang.

Hovedsystemets schema-1-poll på `/api/device/updates` bevares.
Den må ikke få et schema-2-appkatalog. Overgangen til split-kerne
kræver først en observeret bro-opdaterer; bevarede apps bindes til den
konkrete corebestilling og det accepterede katalog. Appordrer skal
afvente en uafsluttet autoriseret migration. Afbrudt migration må ikke
meldes færdig alene ud fra kernens versionsnummer.

De præcise producentkommandoer står i
[ny-app-guiden](NY-APP-GUIDE.md), den native byggeindgang i
[packaging/README.md](../system/linux-elevhub/packaging/README.md).
Faktisk installation, genoptagelse, databevaring, opstart og offline
genstart kræver særskilte prøver efter
[udgivelsesprocessen](UDGIVELSESPROCES.md).
