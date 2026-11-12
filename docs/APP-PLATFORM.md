# Værksteder som apps

Designnote, 12. november 2026. Den beskriver, hvor Makerværkstedet skal hen
med værkstederne på elevenhederne. Den beskriver også, hvad der findes nu,
og i hvilken rækkefølge resten bygges. Noten er ikke en udgivelse. Hvert trin
følger [udgivelsesprocessen](UDGIVELSESPROCES.md).

## Retning (fastlagt af projektejeren)

- **Serveren er altid tændt og ejer køerne.** Print, laser, godkendelser og
  maskiner styres centralt. Køerne flyttes ikke ud på enhederne.
- **Elevenheden kan bruges uden server.** Værkstederne kører lokalt.
  Afleveringer gemmes i en lokal udbakke og sendes, når serveren svarer.
- **Hvert værksted er en app-pakke.** Skolen vælger centralt, hvilke apps
  enhederne har. Grund-ISO'en er en tom tavle, og en ny enhed får skolens
  valgte apps ved tilmelding.
- **3D-print som upload findes ikke på elevenheden.** Den erstattes af
  3D-værkstedet (`web/model3d.html`) med *Send til print*. Den offentlige
  printside (`web/index.html`) bliver på serveren til elever og lærere uden
  elevenhed.
- **Åbent økosystem.** Andre skal kunne lave apps. Indtil videre kommer en ny
  app som commit på GitHub. Claude eller Codex gennemgår den, og
  projektejeren godkender den. Et fælles katalog kommer senere.

## Status 12/11

| Del | Nu | Mangler |
|---|---|---|
| Laserværkstedet | Ny lokal editor i `web/laservaerksted/`. Fælles metadata, `.laser.json`, hylde/direkte genåbning og fortsæt/gem/kassér er afprøvet i faktisk Chrome; UI/UX-review ved 1024×688. | Gamle `.projekt.svg`-laserprojekter kan endnu ikke åbnes i den nye editor. |
| Laser-udbakke | `laser_local` validerer lokalt med `clean_svg`, gemmer i `/var/lib/elevhub-design/udbakke` ved manglende forbindelse, svarer 202 og sender igen hvert 30. s med samme `requestId`. | Visning i editoren af, hvor mange afleveringer der venter. |
| Folieværkstedet | Fælles projektflow i den eksisterende folieeditor (`web/folie.html`). Dummytekst oprettes ikke før direkte projektåbning på en administreret enhed. Otte Chrome-forløb for metadata, gem/genåbn, fejl og elevskift består. | Ingen ny folieeditor i denne ændring. |
| 3D-værkstedet | Lokal port 8798, STL-import og vedvarende print-udbakke. Fælles klasse/elevnavn/projektnavn, redigerbare `.mkv3d` med importerede figurer, revisioner og ren næste elev. Faktisk WSL- og Hyper-V-installation: gem, tjenestegenstart, genåbn, redigér og gem ny revision består. Native VM-startdialog og Til start er afprøvet. | Samlet ny produkt-ISO er ikke bygget med alle rettelser. |
| Installeret app-katalog | Fem manifester i de eksisterende core- og Scratch-pakker. Autentificeret lokal `GET /apps` læser dem ved hvert kald; startsiden bruger kataloget både online og offline og bevarer lærerens aktuelle lukning. Faktisk WSL-opgradering, Scratch-fjernelse/geninstallation uden kataloggenstart og bevarelse af profil, CA, projekter og ventende print er afprøvet. Renderede browserprøver og UI/UX-review er udført. | Første grafiske opstart er nu afprøvet i lokal Hyper-V. Signeret udgivelse og appopdeling mangler. |
| App-pakker | Fire faste Debian-pakker; bygge-, installations- og opdateringskode læser samme `DEBIAN_COMPONENTS` i `app/simple_release.py`. | Pakke pr. app, skolens valg og tom produkt-ISO. |

## App-pakken

En app er én signeret Debian-pakke `makervaerkstedet-app-<id>` med:

- `usr/share/makervaerkstedet/apps/<id>/app.json`: manifestet.
- Appens statiske filer og eventuelt en lokal tjeneste med egen systemd-unit
  (`DynamicUser`, kun loopback og skolens server, som `elevhub-design`).

Manifestet (første udgave):

```json
{
  "schema": 1,
  "id": "laser",
  "navn": "Laser",
  "version": "2.0.0",
  "start": "http://127.0.0.1:8795/",
  "offline": true,
  "aflevering": "laser-submit",
  "kraever_internet": false,
  "licens": "se pakkens copyright-fil"
}
```

- `id` er unikt og matcher hubbens app-id, så lærerens åben/luk-knap virker
  uændret.
- `aflevering` er én af de faste udbakketyper nedenfor. En app får ingen
  anden vej til serveren.
- Startsiden (`start.html.j2`) bygges fra de installerede manifester i stedet
  for den faste `OFFLINE_APPS`-liste.

I den nuværende overgang ligger laser, folie, 3D og micro:bit stadig i
core-pakken, mens Scratch har sin egen pakke. Manifestet beskriver installeret
indhold; det giver ikke tilladelse til at installere en vilkårlig pakke.
`offline` betyder adgang uden skoleforbindelse. Micro:bits lokale indgang
kræver stadig internet til selve MakeCode-editoren (`kraever_internet`).
Et manglende eller ugyldigt katalog giver ingen gamle lokale app-fliser;
Internet-indgangen forbliver tilgængelig.

## Udbakken

Udbakken ligger i den lokale tjeneste og ikke i browseren. Så overlever en
aflevering, at fanen lukkes, at eleven logger ud, og at enheden genstartes.

Fælles regler (implementeret for laser i `app/laser_local.py`):

1. Tjenesten validerer afleveringen med serverens egne regler, før den
   gemmes. Noget, serveren altid vil afvise, kommer aldrig i køen.
2. Kan serveren ikke nås, gemmes afleveringen som én fil pr. `requestId`, og
   browseren får **202** `{"queued": true}`.
3. En baggrundstråd sender igen med samme `requestId`. Serveren dedublerer,
   så et nyt forsøg aldrig giver to job.
4. Afviser serveren selve afleveringen (400/413 med en besked), flyttes filen
   til `afvist/`, og hændelsen logges. Her bevares højst 200 afviste filer.
   Er værkstedet lukket (423), venter filen. Ved 429 springes filen over i
   denne runde; uventede svar gemmes til et senere forsøg.
5. Udbakken har et loft (50), og eleven får en dansk besked, når den er fuld.

Udbakketyper: `laser-submit` (JSON til `/api/laser/submit`) og `print-job`
(binær STL til `/api/jobs` med `X-Request-ID`). Print opbevarer STL som
base64 i én atomisk JSON-fil. Laser beholder sit eksisterende filformat;
print har egen undermappe og afsendelsesrunde, så lukning af ét værksted
ikke spærrer det andet. Genbrug af et ventende request-id med ændret indhold
afvises uden at overskrive den oprindelige aflevering.

## Skolens valg og tilmelding

- Kontrolpanelet får en liste over godkendte apps. Skolen vælger
  standardsættet.
- Opdateringskanalen (`makervaerkstedet-updater`) installerer og fjerner
  app-pakker efter skolens valg. En ny enhed får sættet ved første kontakt
  efter tilmelding.
- `DEBIAN_COMPONENTS` udvides fra en fast liste til et signeret katalog over
  tilladte app-pakker. Kun pakker i kataloget kan installeres.

## Tillid til apps fra andre

Det her er en sikkerhedsbeslutning, ikke en detalje.

- **Nu:** En app er et commit i projektets GitHub-repo. Claude eller Codex
  gennemgår den, og projektejeren accepterer den. Den bygges og signeres med
  projektets nøgle som alle andre pakker.
- **Krav til en app:** Den lokale tjeneste kører som `DynamicUser` uden
  adgang til elevens hjemmemappe. Netværk er kun tilladt til loopback og
  skolens server. Afleveringer går kun gennem en udbakketype. Appen må ikke
  indeholde skoledata eller nøgler. Tredjepartslicenser følger med.
- **Senere:** et fælles katalog med udgiversignatur, review og
  tilbagekaldelse. Det kræver en særskilt beslutning om nøgler og ansvar.

## Rækkefølge

1. UI/UX-review og afprøvning af den nye laser-editor og *Send til print*
   på en testenhed.
2. Lokal servering af 3D-værkstedet i designtjenesten med `print-job`-udbakke,
   så 3D virker uden server.
3. App-manifest i de eksisterende pakker og en startside bygget fra
   manifesterne.
4. Opdel elevpakken i app-pakker, og gør ISO'en til en tom tavle.
5. Skolens valg i kontrolpanelet og tildeling ved tilmelding.
6. Fælles katalog.
