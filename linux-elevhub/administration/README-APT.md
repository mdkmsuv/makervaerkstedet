# Debian apt updater and offline activation

Normal program updates use apt. School HTTPS authorization and publisher minisign
verification are both required. The publisher verifier is the exact hash-bound
shared app/simple_release.py input; this directory has no verifier fork.

The oneshot service posts only to /api/device/updates with the protected root
identity from /etc/elevhub/enhed.json (computer/token,0600). Root Bearer headers are
never sent on public manifest/signature GETs. Redirects, including HTTPS-to-HTTP,
are rejected. Manifest paths are exact /apt/manifests/<64hex>.json/.minisig under
the configured origin. Manifest bytes/signatures are bounded. The package mapping,
version, architecture, size and SHA must match the verified publisher manifest.
The bundled pinned minisign0.12 and shared verifier pins are in BUILD-PINS.json.
Approved PUBLIC bootstrap keys are included under /usr/share/makervaerkstedet/trust. No production private key is packaged. Only the fixed project GPG keyring is atomically refreshed from the hash-validated updater payload; school CA, identity and locally managed minisign trust are preserved. No key is changed by this release.

Root-only /etc/makervaerkstedet/apt-client.json has exactly schema:1, server_origin,
ca_file, device_file, public_key_file, state_root. Typical paths are respectively
https://school.example:8773, /etc/elevhub/ca.pem, /etc/elevhub/enhed.json,
/etc/makervaerkstedet/release.pub and /var/lib/makervaerkstedet-updater. Config and
credentials are0600, public CA/trust files root-owned without writable group/other
bits, state0700. The UI-readable kiosk.json is rejected as a credential source.
An absent config leaves updater inactive/unconfigured and does not block workshops.

The native Deb822 source is /etc/apt/sources.list.d/makervaerkstedet.sources:
Types: deb
URIs: https://school.example:8773/apt/
Suites: ./
Architectures: amd64
Signed-By: /etc/apt/keyrings/makervaerkstedet.gpg

No Components, Trusted, Allow-Insecure or alternative URI is accepted. Native apt
signature verification is distinct from minisign; neither replaces the other.
The client enforces peer/hostname checks, school-host CA and no redirects from the school host. This host-only redirect rule does not change Debian HTTPS redirect policy. School refresh and exact application downloads are strictly scoped to the validated project source. No unauthenticated/no-verify option
exists. The payload owns no /etc paths. Activation runs the newly installed fixed Python CLI, so self-update uses its new public-keyring pin. Offline activation can copy approved public bootstrap keys into missing local trust files and create the canonical source/client config only when protected existing root identity and CA are already present. It never creates root identity or CA credentials.

A root-only journal stores per-component OFFER revision, manifest hash, publisher
highwater, progress and an outbox. Global response.revision is not the result
revision. All requested component manifests verify before one apt transaction.
Packages download by exact name=version; dpkg-deb metadata and SHA/size are checked
before fixed-argv apt install. Downgrades need the server's explicit rollback for
the exact verified manifest. Actual dpkg installed status/version/architecture,
not apt exit or a stale report, determines success. A restart/self replacement
re-reads inventory and resumes the same desired revision; outbox survives network
loss. No maintainer script restarts the updater during apt. The crash test covers interruption AFTER version commit. Half-configured/interrupted dpkg reports explicit failure/manual-repair-required, with no success acknowledgement or project restore/delete. There is no automatic dpkg recovery framework. Real installed boot/upgrade/powercut and native manual repair remain untested installation gates.

Template activation reads only local UI config, never root identity. It renders
all outputs before replacement, writes an offline-repairable activation receipt,
and atomically replaces start.html, the fixed kiosk-session and local service
units. Prior project/state paths are retained from the protected local service;
no data move/schema/login change occurs. Existing kiosk.json is preserved. Typed
local keyboard/display flags are retained when present. Unrecorded historical
hardware overrides still require migration evidence before physical rollout.

Firstboot without enrollment renders HTTPS-safe local workshop defaults and a
local UI token, with no root/server credentials and no network call. Core template
inputs remain separate from school configuration. Units can be enabled only by
an explicitly authorized installer later. This source/build workflow never
installs packages/dependencies, enables services, boots an OS or modifies /etc,
/var or /home on the development host. Tests use root-owned /tmp trees, synthetic
keys/certificates and an isolated apt adapter; real dpkg ordering/archive inspection
and real pinned Linux minisign verification are exercised.

Native apt owns transitive dependency resolution, including necessary core and
custom Matchbox changes in both directions. The hash-bound core .deb authorizes
its exact dependency through native signed apt metadata. No component resolver,
pin policy or hidden separate order is introduced. Success acknowledges only the
requested verified component/version; every poll reports actual dpkg inventory
for all four components. --no-remove and explicit verified rollback remain.

School-local updates do not require a reachable Debian mirror when already
installed OS dependencies and school packages suffice. Native apt first simulates
the exact local target files using only the validated school source. On success,
installation uses exactly the same source/list scope, target files and flags.
Unused system metadata is not consumed, including old cached indices.

If that native preflight cannot resolve the plan, the updater strictly refreshes
all already configured system/project sources, repeats native simulation in that
scope, and installs with that same scope only after success. A missing or changed
OS dependency therefore still requires a successful system refresh. There is no
blind fallback to stale cache or parsing of apt warning text: TLS/signature and
consumed metadata validity failures remain fatal. Check-Valid-Until stays true.
Manual-repair errors and failures during actual installation are not retried.
The normal --no-remove, explicit verified rollback and actual dpkg inventory/ack
rules are unchanged. No extra mirror, cache service or dependency resolver is
introduced. Native test installs are replaced by --simulate in isolated /tmp APT
roots; these checks do not prove real dpkg installation or boot behaviour.


## Version-bound runtime readiness and explicit bootstrap

Normal activation now requires running local/design/tty1 services plus loopback
health whose startup-snapshot core version, PID and process instance match the
actual installed core and systemd MainPID. Missing/inactive/stale runtime fails;
dpkg status alone cannot succeed. Core descriptor is fixed at
/usr/share/makervaerkstedet/runtime-release.json; its fixed shared helper is
/usr/lib/makervaerkstedet/runtime_health.py. No per-request descriptor reload,
environment release override or overall-feed-tag comparison is used. Unchanged
core identity remains valid for updater-only/Scratch/Matchbox releases.

The explicit --check-config and --check-runtime entrypoints never poll desired
updates. They are used after installation by the reviewed existing-machine
bootstrap. They can initialize the root state directory and are not a replacement
for the wholly read-only bootstrap inspection during a school inventory audit.

See installation/debian/OVERGANG-OG-VEDLIGEHOLDELSE.md for the separately authorized
Debian13 transition, reviewed inventory/payload binding, exclusive updater lock,
named legacy-unit retirement and manual Debian security/reboot routine. Bootstrap
acceptance on a real old generation and actual installed update/reboot trials
remain installation gates.
