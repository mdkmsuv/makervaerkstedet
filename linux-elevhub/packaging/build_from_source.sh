#!/bin/bash
# Build the pupil core and updater packages from this source tree and run the
# install gate together with the already released Scratch and Matchbox packages.
# Runs on Debian 13 amd64 (for example WSL) as root, because the gate needs an
# isolated apt run. Nothing is installed on the build machine.
#
# usage: build_from_source.sh <source root> <build inputs>/linux <build inputs>/vaerktoejer <released debs> <new work folder>
#
# Scratch and Matchbox are unchanged third-party components. Their released
# .deb files are reused byte for byte from <released debs>; rebuilding them is
# a separate task with its own inputs.
#
# The work folder must not exist. It receives:
#   locks/   input locks for the two rebuilt packages (private build material)
#   debian/  the four .deb files that belong together
#   report/  apt configuration, update log and install simulation
set -euo pipefail
if [ "$#" -ne 5 ]; then sed -n '2,17p' "$0"; exit 2; fi
SRC="$1"; INPUTS="$2"; TOOLS="$3"; RELEASED="$4"; WORK="$5"
[ "$(id -u)" = 0 ] || { echo "skal køres som root" >&2; exit 2; }
[ ! -e "$WORK" ] || { echo "arbejdsmappen findes allerede: $WORK" >&2; exit 2; }
mkdir -p "$WORK/locks" "$WORK/debian" "$WORK/report"
PACK="$SRC/linux-elevhub/packaging"
cd "$PACK"
python3 -B prepare_inputs.py --components core updater \
  --source-root "$SRC" --fallback-root "$SRC" \
  --scratch "$INPUTS/scratch-gui-v15.1.1-local-projects-build" \
  --matchbox "$INPUTS/matchbox/matchbox-window-manager" \
  --legacy-manifest "$INPUTS/historical-public-manifest.json" \
  --output-dir "$WORK/locks" \
  --updater-source "$SRC" --shared-verifier "$SRC/app/simple_release.py" \
  --minisign "$TOOLS/minisign-linux/minisign-linux/x86_64/minisign" \
  --minisign-license "$TOOLS/minisign-0.12-LICENSE" \
  --minisign-provenance "$TOOLS/minisign-license-provenance.json" \
  --public-trust-dir "$SRC/deployment/keys"
for name in elev updater; do
  version=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "$WORK/locks/makervaerkstedet-$name.json")
  arch=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['architecture'])" "$WORK/locks/makervaerkstedet-$name.json")
  python3 -B build_debian.py --input-lock "$WORK/locks/makervaerkstedet-$name.json" \
    --output "$WORK/debian/makervaerkstedet-${name}_${version}_${arch}.deb"
done
cp "$RELEASED"/makervaerkstedet-scratch_*.deb "$RELEASED"/makervaerkstedet-matchbox_*.deb "$WORK/debian/"
python3 -B check_install.py \
  --packages "$WORK"/debian/makervaerkstedet-elev_*.deb "$WORK"/debian/makervaerkstedet-matchbox_*.deb \
             "$WORK"/debian/makervaerkstedet-scratch_*.deb "$WORK"/debian/makervaerkstedet-updater_*.deb \
  --debian-keyring /usr/share/keyrings/debian-archive-keyring.gpg --report-dir "$WORK/report"
echo
ls -la "$WORK/debian"
