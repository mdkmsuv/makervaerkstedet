# Testmiljø: elevcomputer i Hyper-V

Dette er en generisk opskrift til et lokalt testmiljø. Den offentlige
kildeudgave indeholder ingen skoleinventar, virkelige VM-navne, adresser,
konti, adgangskoder, nøgler eller lokale arbejdsstier. Brug private
testkvitteringer til konkrete resultater.

## Formål

Testmiljøet kan bruges til at undersøge Elevhub, elevværksteder, køer og
læreradgang på en syntetisk Debian-elevcomputer. Det er en softwareprøve og
er ikke dokumentation for skolens fysiske computere, printere, lasere,
micro:bits eller netværk.

## Overblik

Vælg lokalt en Windows-vært, en Hyper-V-elevcomputer og et isoleret testnet.
Erstat alle værdier med skolens egne private testværdier. Gem dem uden for
repositoryet og send aldrig private nøgler eller adgangsfelter gennem
browseren.

| Del | Generisk testrolle | Lokal beslutning |
|---|---|---|
| Vært | Windows-computer med Hyper-V | Skolens egen maskine |
| Elevcomputer | Minimal Debian amd64-VM | Skolens egen VM-profil |
| Server | Windows-server eller lokal testtjeneste | Skolens valgte rolle |
| Netværk | Isoleret privat testnet med pladsholderadresser | Skolens private adresser |
| Printer/laser | Simuleret backend til køtest | Fysisk hardware kræver særskilt prøve |

## Opsætning

1. Opret en separat testprofil og et isoleret netværk.
2. Brug kun syntetiske navne, adresser, elevprojekter og testnøgler.
3. Kør scripts fra denne mappe med lokale stier og de nødvendige rettigheder.
4. Gem rålogs og resultatkvitteringer uden for den offentlige kildeudgave.
5. Slet testdata efter skolens lokale oprydningsregler, når de ikke længere er nødvendige.

De konkrete PowerShell- og Ansible-scripts i mappen er byggesten til dette
forløb. De indeholder ingen offentlig skoleprofil; udfyldning sker lokalt.

## Kontrol og begrænsninger

En test bør kontrollere elev-/læreradskillelse, køforløb, offlineadfærd,
TLS/SSH-konfiguration og genstart efter den valgte lokale opskrift. Notér
kommando, tidspunkt, versionskilde, input og bestået/fejl/skip i den private
kvittering. En bestået syntetisk prøve beviser ikke installation på skolens
udstyr eller sikker drift med rigtige elevdata.

Brug en isoleret testmappe og syntetiske data til lokal afprøvning.

**Udviklet af Mathias Kusk Damgaard. Kreditering: Klostermarksskolen.**
