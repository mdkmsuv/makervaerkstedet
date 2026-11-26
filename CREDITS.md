# Kreditering

## Projektets ophavskreditering

Projektet må gøres frit tilgængeligt med følgende obligatoriske kreditering:

> Udviklet af Mathias Kusk Damgaard. Kreditering: Klostermarksskolen.

Krediteringen skal følge kildeudgivelser og installationspakker og være synlig
i programmets Om-/krediteringsflade. En generisk skolenamekonfiguration må
ikke fjerne, skjule eller overskrive den.

Projektets egne nye dele er GPL-3.0-or-later med bevaret ophavskreditering;
se [licensens afgrænsning](LICENSE-SCOPE.md). Elevhubs eksisterende MIT-komponent
beholder sin licens. Tredjepartssoftware og særskilte aktiver beholder
deres egne licenser, notices, tilladelser og øvrige vilkår.

De navngivne projektillustrationer i [billedoversigten](system/web/assets/PUBLIC-ILLUSTRATIONS.md)
har CC BY 4.0. Ved deling krediteres Makerværkstedet og Klostermarksskolen;
licenshenvisning og oplysninger om eventuelle billedændringer bevares.
De fire v2-billeder til Folie, Laser, 3D og Scratch er AI-genereret til
Makerværkstedet af projektets AI-assistent, bekræftet af projektejeren
17. november 2026. De følger samme CC BY 4.0-vilkår; se billedoversigtens
særskilte provenance.

## Elevindgangens skrifttype

Manrope er leveret i brugerens reference-kit ZIP. Den medfølgende notice
angiver Copyright 2018 The Manrope Project Authors
(https://github.com/googlefonts/manrope), SIL Open Font License 1.1.
Den oprindelige downloadadresse er ikke kendt. Identiske runtime-kopier af
`Manrope.ttf` og `Manrope-OFL.txt` ligger i `linux-elevhub/projekter/web/` og
`linux-elevhub/server/elevhub/web/assets/`; begge fontfiler er fastlåst med
SHA-256 i `publicFonts` i kildeeksportens manifest. Den medfølgende OFL-tekst
har SHA-256 `58172e0c0fac2cda8a37b348164bb55e44b0e69051e557e92b1d3f6910141f7b`.

## 3D-værkstedets lokale biblioteker

Spillebrikkernes 28 meshdele og spillepladegeneratorens geometri er fra
Mathias Kusk Damgaards Brætspilsfabrik på
[Skoleværkstedet](https://xn--skolevrkstedet-5ib.dk/), tilpasset til lokal brug
i 3D-værkstedet og Laserværkstedet. Kilde og udgaver er beskrevet i
[3D-værkstedets appbeskrivelse](apps/3dprint/README.md) og
[Laserværkstedets appbeskrivelse](apps/laser/README.md).

Manifold3.5.4 af Emmett Lalish og bidragydere bruges som lokal geometrikerne (Apache-2.0). Three.js0.183.2 bruges til visning (MIT). 3MF-filer pakkes og udpakkes med fflate 0.8.3 (MIT). Originale licenstekster følger i web/assets/licenses/model3d/. Ingen biblioteker hentes fra CDN under brug.

Tekstfigurer bruger opentype.js 2.0.0 (MIT). Standardvalget er Metrophobic Regular af The Metrophobic Project Authors. Kataloget omfatter også Archivo Black, Titan One, Anton, Alfa Slab One, Bungee, Righteous, Bree Serif, Kalam Bold, Space Mono Bold og Rubik Mono One. Alle skrifter er uændrede statiske TrueType-filer under SIL Open Font License 1.1. [Skriftkilder og kontrolsummer](apps/3dprint/web/assets/model3d/font-provenance.json) og [originale licenser og ophavsnotitser](apps/3dprint/web/assets/licenses/model3d/) følger med.
