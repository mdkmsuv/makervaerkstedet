# Lokal Elevhub-sikkerhed

`server/elevhub/opret_sikkerhed.py` laver en lokal CA, et HTTPS-servercertifikat
og credentials til Elevhub-computere. Scriptet ændrer ikke kørende tjenester.
Det bruger systemets `openssl`; der er ingen egen kryptografi.

Kør det fra Debian WSL i en Linux-home, hvor almindelige Unix-rettigheder kan
håndhæves. Scriptet nægter at oprette hemmeligheder direkte på Windows, fordi
det ikke antager, at NTFS-ACL'er er konfigureret sikkert.

## Første oprettelse

Vælg to nye mapper. `--san` skal angives mindst én gang og må kun være de
DNS-navne eller IP-adresser, som serveren faktisk skal nås på:

```sh
python3 server/elevhub/opret_sikkerhed.py init \
  --output-dir /srv/elevhub/sikkerhed \
  --issuer-dir /srv/elevhub/udsteder \
  --san elevhub.lokalt \
  --san 192.0.2.10
```

Begge mapper oprettes med mode `700`, og eksisterende mapper afvises. Runtime-
mappen indeholder `ca.crt`, `server.crt`, `server.key`, `enheder.json` og
`enheder/`. CA's private udstedernøgle ligger kun i issuer-mappen og er ikke en
runtime-fil. Private nøgler og credentials får mode `600`; registryfilen
indeholder kun SHA-256-hashes.

`server.crt` er gyldigt i 825 dage og CA-certifikatet i 3650 dage. Kontrollér
udløb med:

```sh
openssl x509 -in /srv/elevhub/sikkerhed/server.crt -noout -enddate
openssl x509 -in /srv/elevhub/sikkerhed/ca.crt -noout -enddate
```

Forny i god tid ved at oprette en ny, eksklusiv bundle med `init`, installere
den efter en planlagt serviceændring og distribuere den nye `ca.crt` til
klienterne. En CA-udskiftning kræver, at alle klienter får den nye trust anchor;
den gamle bundle skal bevares som backup, indtil den er udfaset. Scriptet
sender ikke certifikater eller credentials over netværket.

## Enheder

Tilføj en computer eksplicit:

```sh
python3 server/elevhub/opret_sikkerhed.py add-device \
  --output-dir /srv/elevhub/sikkerhed --name <ELEV-VM>
```

Der skrives en ny `enheder/<ELEV-VM>.json`; tokens vises aldrig i terminalen.
Enhedsnavnet følger serverens format: små ASCII-bogstaver, tal og bindestreg,
1-32 tegn, med bogstav/tal først og sidst. En eksisterende enhed overskrives ikke.
Ved mistanke om lækage eller udskiftning
bruges den særskilte, bekræftede rotationskommando:

```sh
python3 server/elevhub/opret_sikkerhed.py rotate-device \
  --output-dir /srv/elevhub/sikkerhed --name <ELEV-VM> --confirm
```

Rotation ændrer kun den valgte enheds credentials og den atomisk udskiftede
`enheder.json`; CA og servercertifikat roteres ikke. Klientfilen skal kopieres
via en krypteret eller fysisk sikker administrationskanal. Brug aldrig
ukrypteret HTTP til credentials.

## Test

På Linux/WSL:

```sh
python3 -m unittest discover -s server/elevhub -p 'test_opret_sikkerhed.py'
```

Certifikatverifikation, SAN, hash-match, token-unikhed, filrettigheder,
afvisning af overskrivning, navnevalidering og rotation testes. En lokal
socketpair bruges kun til at kontrollere certifikatet gennem Python 3.13's
strict `ssl.create_default_context`; der startes ingen permanent
netværkstjeneste. På Windows springer integrationstestene over med en
forklaring, hvis `openssl` eller Linux-rettigheder ikke er tilgængelige.
