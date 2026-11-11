# Køfiler og oprydning

Printværkstedets SQLite-kø er historik. Selve elevens STL og slicerens arbejdsmappe er midlertidige jobpayloads.

Når et printjob først har fået en durable terminal status (`done`, `failed` eller `cancelled`), sletter køen kun:

- `school/uploads/<job-id>.stl`
- `school/sliced/<job-id>/` (inklusive G-code, 3MF, slicerlog og midlertidige filer)

Status skal være skrevet og committet til SQLite før oprydningen forsøges. Historikraden bevares, så lærerens køoversigt fortsat kan vise filnavn, status og besked. `preparing`, `dispatching`, `starting`, `printing`, `recovering`, `attention` og `queued` beholder payloaden. En retrybar klargøringsfejl bliver derfor i køen. En uafklaret start (`attention`) bliver aldrig slettet automatisk.

Oprydningen bruger kun exact-match job-id'er og de to kendte kømapper. Symlinks afkobles som links og følges aldrig. Hvis Windows stadig holder en fil åben, bliver status stående, fejlen logget, og jobbet sættes til en afgrænset retry efter 60 sekunder, højst 16 jobs pr. worker-tick. Startup gennemgår fortsat alle terminalrækker som crash-recovery.

Laserlærerens inbox bruger SVG'en inline i sin kødatabase og har derfor ingen separat SVG-payloadfil. Når en aflevering markeres `done`, committer inboxen først status og sletter derefter SVG'en og billedernes valg i `image_settings`. Den kompakte historik (`id`, elev, klasse, projekt, tidspunkt, status og dimensions-/operationsmetadata) bevares i 30 dage efter afleveringen og slettes derefter helt, inklusive elevens navn og klasse (`delete_expired_history`, GDPR-dataminimering; kører ved opstart og hver gang en aflevering markeres færdig). Afleveringer, der stadig venter hos læreren, slettes aldrig. `payload_available=false` gør, at API'et svarer 404 og lærerens UI skjuler forhåndsvisning og hent-links. Nye inbox-databaser bruger SQLite incremental auto-vacuum, så frigivne sider kan gives tilbage til disken efter terminal oprydning. Ældre databaser forsøges migreret med én startup-`VACUUM`; hvis disken er fuld eller databasen er låst, logges det og inboxen fortsætter, mens næste opstart kan prøve igen. Der oprettes ingen fuld backupkopi. Oprydningen køres også ved opstart og er idempotent, så en afbrudt overgang kan fuldføres næste gang. `received` og `ready` beholder payloaden.

Laser-serveren har kun sin kødatabase og midlertidige kødata i den mappe, den startes med. Den opretter ikke et generelt elevprojektlager. Laserstationens gemte plader er særskilte lærer-/produktionsartefakter uden for køoprydningen og slettes ikke automatisk.

## Tidligere kameraarkiver

Eksisterende `training-samples/` og andre kameraarkiver bliver ikke rørt af
køoprydningen eller slettet af en opdatering. Denne udgave indsamler ikke
træningsbilleder og starter ingen separat kameratjeneste; tidligere indstillinger
kan ikke aktivere indsamling. Arkiverne er lokale driftsdata, som skolen kan
sikkerhedskopiere og administrere særskilt.
