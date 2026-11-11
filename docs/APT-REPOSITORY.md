# Flat apt publication contract

Production Deb822 file /etc/apt/sources.list.d/makervaerkstedet.sources:

    Types: deb
    URIs: https://CONFIGURED-SERVER/apt/
    Suites: ./
    Architectures: amd64
    Signed-By: /etc/apt/keyrings/makervaerkstedet.gpg

There is no Components field for an exact-path suite. The dedicated server
public-apt-cache contains pool/*.deb, Packages, Packages.gz, Release, InRelease
and Release.gpg. dpkg-scanpackages --multiversion retains exact older package
versions for explicit rollback. Both all and amd64 packages are indexed.
Release includes hashes/sizes of indexes and deliberately omits Valid-Until: project releases do not expire. Native GPG signs apt
metadata; minisign signs the separate desired-component manifest. They are
separate trust keys/protocols. Never use trusted=yes, allow-insecure, disabled
TLS verification or unbounded system apt-key trust. Keep Acquire::Check-Valid-Until=true; Debian sources retain their normal expiry protection.

scripts/build_apt_repository.py uses existing dpkg-deb/dpkg-scanpackages and
apt-ftparchive; in WSL it can call an explicitly pinned existing Windows GPG.
Every input package is checked against its declared SHA256, size and native
control fields, and the shared package/architecture allowlist. Output is new
and separate from the explicitly supplied private signing homedir. It generates
no key, installs no package and does not publish. Public key export is allowed;
private receipts/source paths stay outside the served repository.

Production keys require explicit authorization and are provisioned separately; no key is created by the repository builder. Tests using file: sources prove
apt/GPG metadata validation, not production HTTPS/CA/boot/service behavior.

Publication input requires `install_gate: {path, sha256}` binding the private
`release-build.json` receipt from the native Debian13/amd64 empty-status
installation simulation. The receipt must say all_five_simulated=true and
actual_install=false, with exact package names, versions, architectures, file
names and SHA256 for all five allowlisted packages. Missing, failed, changed
or mismatched evidence is rejected before native commands or signing.
This proves dependency resolution in a clean simulated target, not installation.
Receipt/local paths are private; the public repository contains no receipt.

The simulation's Debian archive keyring is pinned to SHA256
506b815cbb32d9b6066b4a2aa524071e071761e7e7f68c3ac74f3061ba852017.
Changing that trusted input requires an explicit reviewed pin update. Private
producer receipts also record the producer source hash and informational UTC
build time; neither imposes a release deadline. A hash-pinned local simulation
receipt catches accidental mutation or use with different packages/keyrings.
It is not independent cryptographic attestation against a malicious publisher
who controls both the receipt and the release signing key.
