# Ansible: ens opsætning af elevcomputerne

Dette dokument beskriver den generiske Ansible-struktur til Linux-
elevcomputere. Den offentlige kildeudgave indeholder ingen skoleinventar,
virkelige adresser, VM-navne, konti, private nøgler eller pilotkvitteringer.
Udfyld skolens egne værdier i en privat inventory-fil uden for repositoryet.

## Hvad rollerne dækker

Rollerne kan sætte elevbjælke, kiosk, USB-håndtering, værksteder,
HTTPS-klient, sikkerhedsopdateringer og valgfri projektbackup op. CaDoodle,
administratorkonto og backup er lokale valg og kræver særskilt afprøvning.
En registreret maskine er ikke dermed automatisk understøttet hardware.

`inventory/skole/` er en skabelon. Den skal udfyldes lokalt med skolens
computeridentiteter, serveradresse og valgte SSH-/TLS-materiale. Private
SSH-nøgler, scripts og vilkårlige serverstier må ikke sendes gennem browseren.

## Lokal controller

Ansible køres typisk fra Debian i WSL på lærerens Windows-computer. Vælg en
lokal driftskonto efter skolens politik, og opbevar private nøgler i den lokale
SSH-agent eller en privat nøglemappe med passende rettigheder.

Eksempel med en lokal repositorysti:

```powershell
wsl -d Debian -- bash "PRIVATE_REPOSITORY_PATH/linux-elevhub/ansible/koer.sh" ping
wsl -d Debian -- bash "PRIVATE_REPOSITORY_PATH/linux-elevhub/ansible/koer.sh" tjek
wsl -d Debian -- bash "PRIVATE_REPOSITORY_PATH/linux-elevhub/ansible/koer.sh" koer
```

Erstat `PRIVATE_REPOSITORY_PATH` lokalt. Brug skolens egne adresser og
firewallregler; dokumentér dem i en privat opsætning.

## Kontrol før og efter kørsel

Kør først en tørkørsel. Kontroller derefter elev-/læreradskillelse,
SSH-værtsnøgler, kioskadgang, værkstedsadgang, offlineadfærd og eventuel
backup. Gem tidspunkt, kommando, kildeversion, input og bestået/fejl/skip i en
privat kvittering. En syntetisk prøve eller VM-prøve er ikke dokumentation for
skolens fysiske computere, printere, lasere eller micro:bits.

Brug en separat testmaskine og et aftalt vedligeholdelsesvindue, før ændringer
anvendes på skolens elevcomputere.
Den daglige drift styres af læreren gennem kontrolpanelet; der følger ingen
særskilt driftsagent, agentnøgle eller agent-CLI med.

**Udviklet af Mathias Kusk Damgaard. Kreditering: Klostermarksskolen.**
