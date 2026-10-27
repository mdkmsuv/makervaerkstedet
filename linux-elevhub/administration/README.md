# Elevhub administration fra Windows

Dobbeltklik på `Open-Elevhub-Administration.cmd`. Computerlisten er som
standard `ansible/inventory/skole/fra-serveren.py`, der henter de tændte
elevcomputere fra serveren. Vælg eventuelt en vært, og vælg **CaDoodle** eller
**Hele elevsystemet**.
**Tjek ændringer** bruger `koer.sh tjek`; **Anvend opdatering** bruger
`koer.sh koer`. Output åbnes i et separat PowerShell-vindue, så brugeren ikke
skal skrive kommandoer.

Launcheren bruger den eksisterende `ansible/koer.sh`, som sætter
`ANSIBLE_CONFIG` eksplicit. Den ændrer ikke SSH-værtsnøgler, credentials eller
private nøgler og slår ikke host-key checking fra. Windows-stier konverteres
med `wslpath`, og argumenterne sendes som separate argumenter.

`Hele elevsystemet` viser en ekstra advarsel, fordi baggrundstjenester kan blive
genstartet. En elevskærm, der er i brug, genstartes ikke; ændringen virker fra
næste opstart (`-e kiosk_genstart_nu=true` tvinger genstart). Standardvalget er
**CaDoodle**. Vinduet starter ingen opdatering, før brugeren vælger en handling.
Ved en CaDoodle-JAR-opdatering håndterer den eksisterende rolle en åben
CaDoodle ved at springe den maskine over med en tydelig fejltekst; de øvrige
maskiner fortsætter. Elevprojekter slettes ikke af opdateringsværktøjet.

Elevcomputerne behøver ikke faste IP-adresser. Hver computer melder sig til
serveren med sin egen enhedsnøgle og sender sin adresse og offentlige
SSH-værtsnøgle med. `fra-serveren.py` henter listen over den fastlåste
SSH-forbindelse til serveren. SSH til elevcomputerne godkendes under computerens
navn, ikke dens IP-adresse, og kun med den nøgle, serveren har modtaget fra netop
den computer. En computer, der er slukket (ikke set i 15 minutter), springes
over. Hvis serveren endnu ikke har modtaget nøglen, afvises computeren med en
besked. Ansible viser den som `[ERROR]: Elevhub: …`, men de øvrige computere
fortsætter. Oversigt uden Ansible:

```bash
python3 ansible/inventory/skole/fra-serveren.py --vis
```

Den statiske `hosts.yml` kan stadig vælges, fx til en enkelt maskine uden
serverforbindelse.

Kør parser/argumenttest uden WSL-kørsel:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Elevhub-Administration.ps1 -Test
```
