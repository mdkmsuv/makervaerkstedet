# micro:bit direkte fra MakeCode

Elevcomputerens kiosk er sat op til at forhåndsgodkende micro:bit via USB
for `https://makecode.microbit.org`. Linux giver samtidig elevkontoen de
nødvendige rettigheder til selve USB-enheden. Opsætningen følger med
Ansible-rollen `kiosk`.

## Elevens arbejdsgang

1. Sæt én micro:bit i **elevcomputerens** USB-port med et datakabel.
2. Åbn micro:bit-værkstedet og opret eller åbn et projekt i MakeCode.
3. Vent på forbindelsen, og tryk **Hent / Download** i MakeCode.

MakeCodes WebUSB-funktion overfører programmet direkte. Det kræver hverken
kopiering til MICROBIT-drevet eller en ekstra downloadknap i Elevhub.
Programmet overføres først, når eleven trykker Download. Enheden kan også
sættes i, mens editoren er åben. Brug én micro:bit ad gangen pr. station,
så det er tydeligt, hvilken enhed der modtager programmet.

**Gem også projektet til senere brug.** En direkte overførsel til micro:bit
opretter ikke automatisk en lokal projektkopi. Brug MakeCodes gem-funktion
og behold filen i Projekter eller på elevens almindelige USB-nøgle.
MICROBIT-drevet bliver fortsat ikke brugt som fælles downloadmappe for
Scratch, tegninger og andre værksteder.

## Sådan er det afgrænset

- Chromiums `WebUsbAllowDevicesForUrls` giver kun automatisk tilladelse
  til MakeCodes præcise HTTPS-origin og enheder med VID `0d28`, PID `0204`
  (decimalt 3368 og 516). Ingen wildcard-hjemmesider eller andre USB-id'er.
- `/etc/udev/rules.d/60-elevhub-microbit.rules` giver den konfigurerede
  elevkonto ejerskab over netop disse USB-enhedsfiler med mode `0600`.
  Eleven får ingen nye grupper eller sudo-rettigheder. Reglen omfatter
  ikke almindelige USB-diske, tastaturer eller andre USB-id'er.
- Ansible genindlæser udev og anvender reglen på allerede tilsluttede
  matchende enheder. En ændret browserpolitik genstarter kiosksessionen;
  elevprojekter skal være gemt før udrulning.
- MakeCode bruger sin egen understøttede WebUSB-forbindelse. Elevhub
  indsprøjter ingen JavaScript-kode i editoren og kører ingen automatisk
  firmwareoverførsel i baggrunden.
- Datatræneren og andre hjemmesider får ikke samme automatiske
  browsertilladelse. Deres normale forbindelsesvalg er bevaret.

Lærerens netværks- og værkstedsvalg er uændrede. Almindeligt Internet kan
fortsat være lukket, mens det åbne micro:bit-værksted når MakeCode via
værkstedets domæneregler. MakeCode er stadig en online editor; dette er
ikke en lokal installation af MakeCode.

## Hvis forbindelsen ikke kommer

- Brug et **USB-datakabel**; nogle kabler kan kun oplade.
- Sæt enheden direkte i Linux-stationen. USB i Windows-serveren eller
  administratorcomputeren er ikke automatisk tilgængelig på elevstationen.
- Prøv at tage kablet ud og sætte det i igen. Luk andre editorer, som
  bruger samme micro:bit, og åbn MakeCode igen.
- MakeCodes forbindelsesmenu kan bruges som reservevej. Hvis den viser
  browserens enhedsvælger, kontrollér, at den administrerede politik er
  installeret, og at adressen er `https://makecode.microbit.org`.
- Ældre micro:bit V1 kræver DAPLink-firmware **0249 eller nyere** til
  WebUSB. En lærer skal kontrollere og eventuelt opdatere firmwaren efter
  producentens vejledning. Elevhub opdaterer den ikke automatisk.

På en fysisk Debian-station kan administratoren kontrollere identiteten
med `lsusb -d 0d28:0204` (pakken `usbutils`). En tilsluttet enhed skal have
en fil under `/dev/bus/usb/`, der kan læses og skrives af elevkontoen.
Browserpolitikken ligger i `/etc/chromium/policies/managed/elevhub.json`.

## Verifikation og resterende fysisk prøve

Opsætningen er lavet til den native Chromium-pakke på Debian. En Snap-
eller Flatpak-browser kan kræve særskilt adgang til USB.

Kontrollér Ansible-udrulning, root-ejede udev-regler, browserens USB-politik
og adgangsgrupper på hver relevant Linux-type. En softwareprøve erstatter
ikke fysisk tilslutning og overførsel til en micro:bit.

MakeCodes upstream-kode forsøger genforbindelse efter editorstart og ved
USB-tilslutning via `navigator.usb.getDevices()`. Download springer
parringsforløbet over, når forbindelsen allerede er etableret. Dette er
kontrolleret i kildekoden, ikke ved en simuleret succesbesked i Elevhub.
Hvis enhedens initialisering fejler, kan MakeCodes normale parrings- eller
fejlforløb stadig vises.

Automatisk genkendelse, overførsel og kørsel skal verificeres med fysisk hardware. En micro:bit sat i Windows-værten bliver heller ikke
automatisk videresendt til Hyper-V-elevmaskinen.

Før skoleudrulning afprøves på en fysisk elevstation: tilslut før og efter
åbning af editoren, overfør et LED-program, frakobl og tilslut igen, skift
til en anden micro:bit og gentag efter genstart. Kontrollér desuden, at
et gemt projekt kan åbnes igen, og at almindeligt Internet fortsat er
lukket under hele prøven.

## Primære kilder

- [MakeCode: WebUSB og direkte Download](https://makecode.microbit.org/device/usb/webusb).
- [Micro:bit Foundation: WebUSB-fejlfinding og Linux-rettigheder](https://support.microbit.org/support/solutions/articles/19000105428-webusb-troubleshooting).
- [Micro:bit Foundation: enhedernes VID/PID](https://support.microbit.org/support/solutions/articles/19000035697-what-are-the-usb-vid-pid-numbers-for-micro-bit).
- [Chromiums policydefinition og præcise skema](https://github.com/chromium/chromium/blob/main/components/policy/resources/templates/policy_definitions/ContentSettings/WebUsbAllowDevicesForUrls.yaml).
- [MakeCodes USB-registrering og genforbindelse](https://github.com/microsoft/pxt/blob/master/pxtlib/webusb.ts),
  [editorstart og Download](https://github.com/microsoft/pxt/blob/master/webapp/src/app.tsx)
  og [micro:bit-targetets USB-filtre](https://github.com/microsoft/pxt-microbit/blob/master/editor/extension.tsx).
