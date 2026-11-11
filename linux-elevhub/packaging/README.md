# Debian packages

`build_debian.py --input-lock <private.json> --output <new.deb>` builds with
existing `dpkg-deb`. It never installs dependencies, packages or services.

The input schema `makervaerkstedet.debian-inputs.v1` has package, version,
architecture, depends, provenance, and files. Each file supplies source, relative
package path, SHA256 and integer mode (420 or 493). All bytes are verified before
staging. Only program paths under usr/ are accepted. No maintainer scripts, local
identity, credentials, /etc, /var or /home content is packaged. Existing data and
configuration are outside package ownership. The input lock is private build
material; only the separate receipt records provenance and relative file hashes.

Debian supplies Python and native dependencies. No embedded Python, Java or
CaDoodle is accepted. Keep Scratch and the patched Matchbox in their
own packages so program fixes do not resend large unchanged assets. Retain each
upstream license and required corresponding source with its app package.

Preserve current runtime paths under /usr/local/lib/elevhub and
/usr/local/share/elevhub when assembling the reviewed core. This compatibility
layout is deliberate; converting paths and enabling services is a separate,
explicit installation contract. /etc/elevhub, /var and /home/elev/Projekter remain
outside these packages. Native apt repository signing remains separate from
minisign release metadata.

Builds normalize modes/owners/mtime and use xz with SOURCE_DATE_EPOCH. Verify with
dpkg-deb --info, --contents, and --extract into a synthetic directory. An inspected
or extracted package is not evidence of installation, graphical login, boot,
updates or hardware operation. The first core must include the integrated reviewed
UI, local design runtime/web allowlist and kiosk/project components; a project API
subset alone is not the complete student program.

A complete release MUST use `build_release.py --input-locks <four locks>
--output-dir <artifacts> --debian-keyring <Debian archive public keyring>
--report-dir <evidence>`. `build_debian.py` produces intermediate archives only.
The release command stops on any failure and writes `release-build.json` only
AFTER real `apt-get update` and `apt-get install --simulate` of all four exact
local archives succeed. `check_install.py` can repeat this gate independently.
It requires existing Debian13/amd64 tools, an empty dpkg status and temporary
APT config/state/cache/log/trust paths under /tmp. Host hooks are absent and the
configuration dump is checked before network access. Debian indices retain
normal native signature, TLS and Valid-Until verification. No apt installation
or service command is permitted by this gate. Simulation proves dependency
resolution, not maintainer-script execution, graphical startup or boot.

Updater tests require Linux root and `MKV_TEST_MINISIGN=<pinned Linux binary>`;
use `MKV_SHARED_DIR=<canonical app directory>` when not integrated in app/.
Activation tests accept `MKV_TEMPLATE_DIR=<reviewed templates>` and
`MKV_PUBLIC_TRUST_DIR=<public signing-key directory>`. Missing mandatory inputs
raise an error rather than reporting a skipped green release gate. Native HTTPS
verification requires `check_native_apt.py --server-source <server/elevhub>` and
uses actual Hub/Server/SimpleUpdates classes; no replacement TLS handler.
Our schema1 releases contain no expiry field; Debian's Check-Valid-Until stays
true. Publisher signatures, sequence/fork checks and explicit rollback remain.
