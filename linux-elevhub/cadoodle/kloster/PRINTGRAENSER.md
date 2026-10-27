# Midlertidig blokering ved mange print

Grænsen gælder samlet for CaDoodles **Send til ledig printer** og upload i Printværkstedet.

Som standard modtages de første 9 nye, gyldige print fra en computer inden for 60 minutter. Forsøg på print nr. **10** afvises og blokerer nye indsendelser i **én time fra dette forsøg**. Læreren kan ændre udløseren mellem 2 og 100. Den tidligere regel på 3 print/time og 2 samtidige print er fjernet, inklusive dens gemte indstilling. Værkstedets eksisterende samlede kapacitetsgrænser gælder fortsat.

## Lærerens betjening

Lærersiden viser en besked øverst, når en computer er blokeret. **Se og åbn printadgang** fører til **Midlertidigt blokerede computere**. Listen viser netværksadresse, blokeringstidspunkt og automatisk udløbstidspunkt. **Åbn adgang nu** ophæver blokeringen straks og starter optællingen forfra. Andre computeres adgang og allerede modtagne print påvirkes ikke.

**Gem regel** ændrer, hvilket printforsøg inden for en rullende time der fremover udløser blokering. Eksisterende blokeringer beholder deres udløbstidspunkt og kan ophæves enkeltvis. Alle lærerhandlinger kræver lærerkode; elevindgangen afviser både listen og handlingerne.

## Køens adfærd

- Automatisk udløb sker efter højst én time uden en åben lærerbrowser. Flere forsøg forlænger ikke blokeringen. Genstart bevarer udløbstidspunktet.
- Manuel ophævelse og automatisk udløb giver en frisk optælling. Tidligere print kan stadig ligge i kø. Et gammelt eller dobbelt klik på en ophævet blokering kan ikke ophæve en senere blokering.
- Alle accepterede indsendelser tæller med, også afsluttede, fejlede og annullerede print. Ugyldige filer og afviste indsendelser tæller ikke. Det udløsende forsøg bliver ikke et printjob.
- Samme request-id, kilde, fil og indstillinger giver den eksisterende kvittering, også under blokering. Gensendelse efter et tabt svar tæller derfor ikke igen.
- Kontrol, blokering og oprettelse sker under samme kølås. SQLite gemmer adgangstilstanden; en række-markør adskiller nye indsendelser fra print før lærerens ophævelse.
- HTTP 429 indeholder en dansk forklaring og Retry-After i sekunder. Den offentlige /api/state viser kun den forespørgende computers print_access. Lærerens oversigt og handlinger ligger under /api/print-access.

## Identitet

Eleverne bruger stadig den fælles konto elev. Reglen følger derfor computerens netværksadresse, ikke en personlig elevkonto. Skift af computer giver adgang via den anden computer. Personlig kontrol på tværs af computere kræver autentificerede elevkonti eller elevkoder.

Elevhub overskriver X-Print-Source med forbindelsens faktiske IP-adresse. Printserveren accepterer kun dette header fra loopback. Backend skal som hidtil være begrænset til loopback; lokale serverprocesser ligger inden for tillidsgrænsen. NAT deler identitet, mens en ændret IP giver en anden identitet. Pilot-VM'en har fast adresse. Dette er praktisk beskyttelse mod masseindsendelser, ikke fuld DoS-beskyttelse.

## Verifikation

Tests i tests/test_print_access.py omfatter 100 samtidige indsendelser: 9 accepteres, resten afvises under én blokering, mens en anden computer fortsat kan sende. De dækker fast udløb, genstart, automatisk genåbning, lærerophævelse, nye blokeringer efter ophævelse, idempotens, rullende timevindue og migration. HTTP- og gatewaytests kontrollerer lærerbeskyttelse, kildeoverskrivning og ventetid. Kildeprøver er adskilt fra en faktisk installations- og printerprøve.

En isoleret browserprøve kontrollerede overblik, genvej, **Åbn adgang nu** og **Gem regel**. Et nyt print blev accepteret straks efter ophævelse. Prøvekøen var pauset og brugte simulerede printere. Hovedskolens Metrophobic-skrift, hvide/grå flader og bordeauxknap er bevaret og visuelt kontrolleret.

VM'ens HTTPS-elevindgang returnerer nu åben print_access; den gamle kvote er væk. Eksisterende print er bevaret. SQLite-backup før ændringen: <LOKAL-TESTMAPPE>\printvaerksted-test\backups\before-temporary-blocks-20260919.sqlite3.
