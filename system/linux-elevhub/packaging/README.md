# Byg Debian-basis og selvstændige apps

Kør fra produktroden med `REPOSITORY-LAYOUT.json`.
Kilder ligger i `system/`, `apps/<id>/` og `sdk/`; installerede
systemstier og dataområder er fortsat de eksisterende.

## Én app uden genbygning af kernen

For en statisk SDK-app `idebog`, oprettet efter
[ny-app-guiden](../../../docs/NY-APP-GUIDE.md), køres på Debian 13 amd64:

```bash
python -B system/linux-elevhub/packaging/prepare_app_inputs.py \
  --source-root "$PWD" --app idebog --output-dir out/app-idebog
python -B system/linux-elevhub/packaging/build_debian.py \
  --input-lock out/app-idebog/makervaerkstedet-app-idebog.json \
  --output out/app-idebog/makervaerkstedet-app-idebog_1.0.0_all.deb
```

Outputmappen skal være ny. `app.json` ejer version og kompatibilitet;
`files.json` ejer præcis appens filer. Ingen anden app eller kerne skal
ændres for at bygge den. Scratch har et særskilt låst upstream-input;
3D og Folie kræver deres egne faktiske browserbyg og kvitteringer.

## Native releasegate

`build_debian.py` fremstiller et mellemliggende arkiv med `dpkg-deb`.
Inputschema `makervaerkstedet.debian-inputs.v1` binder package, version,
architecture, depends, provenance samt hver kildefils SHA256,
relative pakkesti og mode (420 eller 493). Appfiler må kun ligge i
appens eget træ under `usr/share/makervaerkstedet/apps/<id>/`.

Der medfølger ingen maintainer scripts, skoleidentitet, credentials,
`/etc`, `/var` eller elevhjem. Ejere, modes og mtime normaliseres;
xz og `SOURCE_DATE_EPOCH` bruges til pakken. Licenser og påkrævet
corresponding source skal følge tredjepartsindhold.

En udgivelse bruger `build_release.py` med eksplicit `--release-set`:

| Sæt | Præcist indhold |
|---|---|
| `base` | Elevkerne + opdaterer + Matchbox: tre |
| `app` | Basis + én app: fire |
| `migration` | Basis + Laser, Folie, 3D, Scratch og micro:bit: otte |
| `legacy` | Historiske fire pakker og schema-1-kvittering |

Standardvalget i det lave `build_release.py` er `legacy`; vælg
derfor altid det moderne sæt eksplicit. Det høje
`build_from_source.sh` vælger `base`, medmindre andet angives:

```bash
bash system/linux-elevhub/packaging/build_from_source.sh \
  /sti/til/committet-kilde /sti/til/inputs/linux /sti/til/vaerktoejer \
  /sti/til/tidligere-pakker /sti/til/ny-arbejdsmappe --release-set base
```

Brug `--release-set app --app idebog` eller `--release-set migration`
efter behov. Kilden skal være committet Git eller en verificeret offentlig
eksport. Den portable eksport medtager kun det valgte systems/appens
kildeinputs. Tidligere-pakker-mappen leverer den præcise genbrugte
Matchbox-pakke og dens inputkvittering samt Scratch-baseline ved behov.
Der installeres intet på byggemaskinen.

Gaten kræver Debian 13 amd64/root og eksisterende native værktøjer.
`check_install.py` kører reel `apt-get update` og
`apt-get install --simulate` med tom dpkg-status, isolerede apt-
mapper og uden værtens hooks. Signatur, TLS og Debians normale
`Valid-Until`-kontrol bevares. `release-build.json` skrives først,
når det præcise sæt består; den siger `actual_install: false`.

## Distribution og faktisk prøve

[apt-kontrakten](../../../docs/APT-REPOSITORY.md) beskriver GPG-
repositoryet og minisign-appkataloget. Ingen af byggerne opretter
private nøgler eller publicerer. Brug eksisterende signer og de
kvitteringsbundne producentkommandoer i
[ny-app-guiden](../../../docs/NY-APP-GUIDE.md).

Native updaterprøver kræver Linux root og
`MKV_TEST_MINISIGN=<fastlåst Linux-værktøj>`. Activation kan modtage
`MKV_TEMPLATE_DIR` og `MKV_PUBLIC_TRUST_DIR`; en manglende obligatorisk
forudsætning skal fejle. `check_native_apt.py --server-source`
afprøver den rigtige Hub/Server/SimpleUpdates HTTPS-vej.

Arkivinspektion og apt-simulering beviser hverken faktisk installation,
kiosklogin, tjenester, genstart, migrationsbevaring eller ISO-boot.
Registrér den separate evidens i udgivelsens kvittering efter
[udgivelsesprocessen](../../../docs/UDGIVELSESPROCES.md).
