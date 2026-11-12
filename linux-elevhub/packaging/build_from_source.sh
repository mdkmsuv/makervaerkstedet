#!/bin/bash
# Build core, updater and Scratch from reviewed inputs and run the install gate
# with the already released Matchbox package.
# Runs on Debian 13 amd64 (for example WSL) as root, because the gate needs an
# isolated apt run. Nothing is installed on the build machine.
#
# usage: build_from_source.sh <source root> <build inputs>/linux <build inputs>/vaerktoejer <released debs> <new work folder>
#
# The packages are built from an export of the committed HEAD of <source root>
# (git archive), never from the working folder. Uncommitted and untracked files
# cannot enter a package, and line endings are those in Git, so the same commit
# gives the same packages. Only the git-ignored web/vendor bundle is copied in,
# and prepare_inputs.py checks it against its build manifest.
#
# Scratch assets are checked byte for byte against the historical inventory.
# Matchbox is unchanged and reused from <released debs>.
#
# The work folder must not exist. It receives:
#   locks/   input locks for the three rebuilt packages (private build material)
#   debian/  the four .deb files that belong together
#   report/  apt configuration, update log and install simulation
set -euo pipefail
if [ "$#" -ne 5 ]; then sed -n '2,17p' "$0"; exit 2; fi
SRC="$1"; INPUTS="$2"; TOOLS="$3"; RELEASED="$4"; WORK="$5"
[ "$(id -u)" = 0 ] || { echo "skal køres som root" >&2; exit 2; }
[ ! -e "$WORK" ] || { echo "arbejdsmappen findes allerede: $WORK" >&2; exit 2; }
mkdir -p "$WORK/locks" "$WORK/debian" "$WORK/report" "$WORK/source"
GIT=(git -C "$SRC" -c safe.directory="$SRC" -c core.autocrlf=false -c core.eol=lf)
COMMIT=$("${GIT[@]}" rev-parse HEAD)
"${GIT[@]}" archive --format=tar "$COMMIT" | tar -x -C "$WORK/source"
cp -a "$SRC/web/vendor" "$WORK/source/web/vendor"
DIRTY=$("${GIT[@]}" status --porcelain --untracked-files=no | wc -l)
printf '{"commit": "%s", "uncommitted_tracked_files_not_built": %s}\n' "$COMMIT" "$DIRTY" > "$WORK/report/source.json"
echo "Bygger commit $COMMIT. Ændringer, der ikke er committet ($DIRTY filer), kommer ikke med."
ORIG="$SRC"
SRC="$WORK/source"
PACK="$SRC/linux-elevhub/packaging"
cd "$PACK"
python3 -B prepare_inputs.py --components core updater scratch \
  --source-root "$SRC" --fallback-root "$SRC" \
  --scratch "$INPUTS/scratch-gui-v15.1.1-local-projects-build" \
  --scratch-receipt "$RELEASED/makervaerkstedet-scratch_15.1.1+local.20261104.1_all.inputs.json" \
  --matchbox "$INPUTS/matchbox/matchbox-window-manager" \
  --legacy-manifest "$INPUTS/historical-public-manifest.json" \
  --output-dir "$WORK/locks" \
  --updater-source "$SRC" --shared-verifier "$SRC/app/simple_release.py" \
  --minisign "$TOOLS/minisign-linux/minisign-linux/x86_64/minisign" \
  --minisign-license "$TOOLS/minisign-0.12-LICENSE" \
  --minisign-provenance "$TOOLS/minisign-license-provenance.json" \
  --public-trust-dir "$SRC/deployment/keys" \n  --vendor-built-from "$ORIG"
for name in elev updater scratch; do
  version=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "$WORK/locks/makervaerkstedet-$name.json")
  arch=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['architecture'])" "$WORK/locks/makervaerkstedet-$name.json")
  python3 -B build_debian.py --input-lock "$WORK/locks/makervaerkstedet-$name.json" \
    --output "$WORK/debian/makervaerkstedet-${name}_${version}_${arch}.deb"
done
cp "$RELEASED"/makervaerkstedet-matchbox_*.deb "$WORK/debian/"
python3 -B check_install.py \
  --packages "$WORK"/debian/makervaerkstedet-elev_*.deb "$WORK"/debian/makervaerkstedet-matchbox_*.deb \
             "$WORK"/debian/makervaerkstedet-scratch_*.deb "$WORK"/debian/makervaerkstedet-updater_*.deb \
  --debian-keyring /usr/share/keyrings/debian-archive-keyring.gpg --report-dir "$WORK/report"
echo
ls -la "$WORK/debian"
