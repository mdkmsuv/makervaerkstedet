# Skærmpause: integration og API-kontrakt

`server/elevhub/pause.py` er den trådsikre pause-tilstand, som er koblet på
de eksisterende lærer-sessioner, lærerpanel og elevstatusser. Elevbjælken viser
en pause oven på den åbne app og kvitterer, når den er vist. Funktionen bruger
den eksisterende lærerport og det eksisterende login, også fra en telefon.

## Serverens state

Serveren opretter én instans sammen med `Tilstand`:

```python
pause = PauseStore(args.data)
```

En state ser sådan ud:

```json
{
  "paused": true,
  "version": 3,
  "message": "Gem arbejdet først. Vi samles om lidt.",
  "until": 1770000000.0,
  "issued_at": 1769998200.0,
  "remaining": 1800.0
}
```

`version` er pause-versionen og må ikke blandes sammen med værkstedernes
app-version. En aktiv pause har altid en udløbstid og kan højst vare 7.200
sekunder. Manglende eller ødelagt aktiv state bliver behandlet som ikke-pause.

## Lærerpanel

Den eksisterende cookie- og Origin-kontrol på `Laerer` gælder fortsat.

## LAN fra telefon eller tablet

Den nuværende eksempelkonfiguration binder lærerporten til
`laerer_adresse: 127.0.0.1`, så den kun kan åbnes på serveren. Ved en planlagt
LAN-udrulning skal administratoren sætte `laerer_adresse` til serverens
makerspace-adresse (eller en eksplicit LAN-bindning) og bruge et DNS-navn, der
findes i HTTPS-certifikatets SAN. Telefonen åbner derefter
`https://<servernavn>:8780/` på samme isolerede LAN og logger ind med den
eksisterende lærerkode.

Det kræver, at telefonens trust store kender skolens Elevhub-CA, og at den
valgte netværkszone senere får en begrænset adgangsregel til lærerporten.
Denne ændring åbner ingen port, ændrer ingen firewall og ændrer ikke den aktive
konfiguration automatisk; den skal planlægges og afprøves sammen med skolens
netværksansvarlige.

`POST /api/laerer/pause`

```json
{"paused": true, "message": "Gem først", "expires_in": 1800}
```

`message` er valgfri og begrænses til 280 tegn. `expires_in` er valgfri ved
pause (standard er 7.200 sekunder) og clamped til 7.200. Ved genoptagelse
sendes `{"paused": false}`. Svar:

```json
{"pause": {"paused": false, "version": 4, "message": "", "until": null,
            "issued_at": 1770000000.0, "remaining": 0}}
```

`GET /api/laerer/oversigt` har `pause` på topniveau og et valgfrit
`pause_ack` på hver computer:

```json
{
  "pause": {"paused": true, "version": 3, "message": "Gem først",
             "until": 1770000000.0, "issued_at": 1769998200.0,
             "remaining": 1800.0},
  "computere": [{"navn": "<ELEV-VM>", "online": true,
                 "pause_ack": {"version": 3, "shown": true, "at": 1769998210.0}}]
}
```

Panelet tæller kun online-computere, der har kvitteret med samme version og
`shown: true`.

## Elevstatus

Den eksisterende `POST /api/status` får et valgfrit felt i elevbjælkens body:

```json
{"computer":"<ELEV-VM>", "kilde":"bjaelke", "version":7,
 "pause_ack":{"version":3,"shown":true}}
```

Når requesten er autentificeret som den pågældende computer, kaldes
`pause.acknowledge(computer, version, shown)`. Serverens svar skal indeholde
`pause.state()` på topniveau:

```json
{"apps":[], "version":7, "gaa_hjem":false,
 "pause":{"paused":true,"version":3,"message":"Gem først",
           "until":1770000000.0,"issued_at":1769998200.0,"remaining":1800.0}}
```

Pauseønsket og kvitteringen er bevidst separate. Klienten sender først
`shown:true` på næste pollingrunde, efter Tk har gjort overlayet synligt.
Versioner, der ikke længere er aktuelle, accepteres ikke.

## Kioskklientens adfærd

`pause_overlay.py` dækker hele X11-skærmen, inklusive elevbjælken, med en sort
flade og kun “Skærmpause” i lys Metrophobic. Ingen besked, nedtælling eller
hjælpetekst vises; fristen håndhæves stadig. Geometrien sættes
eksplicit: Matchbox ignorerede tidligere fuldskærmsflaget og efterlod et
200 × 200-vindue. Den globale Tk-grab fanger både mus og tastatur på tværs af
programmer; Escape og almindelige vinduesskift kan ikke fjerne pausen.
`shown: true` kræver et synligt vindue i skærmens størrelse og en aktiv global
grab. Hvis en anden dialog midlertidigt holder input, prøves igen uden at
melde skærmen låst for tidligt. Se [Tk-grab-dokumentationen](https://www.tcl-lang.org/man/tcl9.0/TkLib/Grab.html).

Et netudfald ophæver ikke pausen. Klienten fastholder serverens oprindelige
frist med Linux `CLOCK_BOOTTIME` og frigiver selv ved udløb, også hvis serveren er
utilgængelig. En gentaget besked kan ikke forlænge samme pause. Læreren kan
ophæve den tidligere; klienten henter status hvert tredje sekund.

`pause_safety.py` starter en separat sikkerhedsvagt, før klienten må låse
input. Den kræver et fungerende Linux-pidfd og skriver først en lokal
nedbrudsmarkering. Hvis Tk-løkken ikke svarer i 15 sekunder, eller fristen
overskrides med 2 sekunder, afslutter vagten kun elevbjælkeprocessen.
X11 frigiver dermed dens vinduer og input; Chromium og de native værksteder
fortsætter. Uret tæller også under dvale. En død eller fastfrosset vagt får
den stadig fungerende Tk-løkke til at frigive skærmen.

Markeringen ligger i elevkontoens `.cache/elevhub/pause-safety-*.json`.
Ved normal frigivelse ryddes nedbrudsmarkeringen, mens den oprindelige
frist bevares. Efter et nedbrud eller sikkerhedsudløsning afvises samme
pause, også efter elevbjælkens automatiske genstart. Læreren kan starte en
ny pause med en ny version. Hvis vagten eller markeringen ikke kan
oprettes, forbliver skærmen ulåst, og klienten kvitterer ikke `shown:true`.

Pausen lukker ingen programmer eller dokumenter. Input frigives ved ophør.
Kiosksessionens `srvrkeys:none` fjerner X-serverens genveje til konsolskift og
ophævelse af grabs. Dette er en lås af elevens grafiske session, ikke af den
fysiske tænd/sluk-knap eller administratorens SSH-adgang.

Når den eksisterende `gaa_hjem`-melding er sand, viser klienten et separat,
reversibelt "værkstedet er midlertidigt lukket"-overlay og navigerer ikke til
start. Det bevarer live arbejde i browserfanen. Når serveren igen melder
`gaa_hjem: false`, fjernes overlayet. Hvis en fremtidig statusmodel ændrer
`gaa_hjem` til kun at være en engangshændelse, kan serveren i stedet levere et
stabilt `lukket_app: true/false`-felt, så klienten ved, hvornår overlayet må
fjernes. Klienten accepterer allerede dette felt.
