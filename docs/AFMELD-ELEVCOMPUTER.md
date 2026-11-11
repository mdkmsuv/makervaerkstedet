# Afmeld en defekt eller offline elevcomputer

Åbn den eksisterende skoleadministration på Windows-serveren med en lokal
administratorkonto: `deployment\school-setup\Manage-School.cmd` under den
installerede programmappe (normalt `%ProgramFiles%\ElevhubServer`). Brug
»Kør som administrator«, hvis Windows beder om det.

Vælg **7. Afmeld elevcomputer (også defekt eller offline)**, skriv computerens
registrerede navn, og bekræft navnet. Vent på beskeden om, at afmeldingen er
gennemført. En fejl er ikke en færdig afmelding; samme handling kan prøves igen.

Computeren behøver hverken svare, aflevere en kvittering eller få sine filer
gendannet. Handlingen sletter ingen filer på den. Eventuelt tab af lokale filer
på den defekte computer accepteres. Printer- og laserkøer og deres job røres ikke.

Afmeldingen tilbagekalder enhedens adgang og fjerner dens opdateringsønsker og
tilknytning. Historiske rapporter og auditdata bevares. Bruges navnet igen,
skal den nye computer tilmeldes med en ny engangskode og derefter tilknyttes
opdateringer på ny; gamle ordrer genaktiveres ikke.

Dette genbruger den lokale administratorvej. Der oprettes ingen ny
privilegeret baggrundstjeneste eller fjernadgang til elevcomputeren.
