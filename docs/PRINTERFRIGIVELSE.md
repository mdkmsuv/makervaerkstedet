# Printertilmelding og frigivelse

## Generisk lærerflow

Lærerens printer-UI kan tilføje og redigere én printer ad gangen via den
eksisterende lokale serverfunktion. Tilmeldingen indsamler:

- model;
- synligt navn;
- privat IPv4-adresse på værkstedets LAN;
- serienummer;
- LAN-adgangskode;
- pladetype.

Ved en ny printer er aktivér-feltet som standard slået fra. Læreren kan vælge
at aktivere printeren efter kontrol af identitet, forbindelse, plade og kamera.
Ved redigering bevarer et tomt LAN-felt den eksisterende adgangskode. Hvis
serveren afviser en ændring, vises fejlen; den tidligere opsætning bevares.

Printerens aktive opsætning skal bevares ved en softwareopdatering. En
deltaopdatering må ikke erstatte printere, LAN-koder, certifikatfingeraftryk,
kamerareferencer eller lærerens aktiveringsvalg.

## Plade- og kamerastandard

Manuel pladefrigivelse er standard. Læreren frigiver efter egen kontrol; en
print- eller færdigmelding er ikke i sig selv bevis på en tom plade.

Kameravisning hjælper læreren med at vurdere pladen. Denne udgave har
ingen AI-vurdering eller automatisk pladefrigivelse. Gamle indstillinger
for automatisk frigivelse giver ikke tilladelse til et nyt print. Læreren
skal kontrollere pladen og bekræfte den klar.

## Næste afprøvning

Det foreslåede forløb er: print afsluttet → afventer afhentning → lærer
kontrollerer og rydder pladen → bekræftet klar. Status for afsluttet print,
tom plade og klar til næste job skal vises hver for sig.

En senere QR-kode eller knap ved printeren kan gøre den rigtige printer nem
at finde. Handlingen skal stadig kræve lærerlogin eller en tidsbegrænset
godkendelse. At scanne en QR-kode er ikke bevis på, at pladen er tom.

Kameraet viser et foto uden automatisk vurdering af indholdet.
Temperatur læses ikke af den aktuelle kølogik;
en temperaturvisning eller grænse kræver også særskilt implementering og test.

## LAN og beskyttelse

Brug kun printere på det forventede private LAN. En public pakke må ikke
indeholde skolens aktuelle printerkoder, certifikater, kamerabilleder,
referenceområder eller andre skoleprofiler. Testdata og træningsbilleder skal
holdes uden for kilde- og releaseeksporten.

Målhardwaretest og modeldækning skal stadig gennemføres. Denne side beskriver
den aktuelle generiske onboarding og dens pilotbegrænsninger.
