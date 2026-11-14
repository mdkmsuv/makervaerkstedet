#!/bin/bash
# Build an exact modular release from committed Git or a verified public export.
# Usage: build_from_source.sh SOURCE INPUTS_LINUX TOOLS RELEASED NEW_WORK
#        [--release-set base|app|migration] [--app ID]
# Debian 13 amd64/root install simulation is enforced by build_release.py.
# No package, service, signing key or repository on this machine is changed.
set -euo pipefail
if [ "$#" -lt 5 ]; then sed -n '2,6p' "$0" >&2; exit 2; fi
SRC="$1"; INPUTS="$2"; TOOLS="$3"; RELEASED="$4"; WORK="$5"; shift 5
RELEASE_SET=base; APP=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --release-set) [ "$#" -ge 2 ] || exit 2; RELEASE_SET="$2"; shift 2 ;;
    --app) [ "$#" -ge 2 ] || exit 2; APP="$2"; shift 2 ;;
    *) echo "ukendt argument: $1" >&2; exit 2 ;;
  esac
done
APPS=()
case "$RELEASE_SET" in
  base) [ -z "$APP" ] || { echo '--app kræver --release-set app' >&2; exit 2; } ;;
  app) [ -n "$APP" ] || { echo '--release-set app kræver --app ID' >&2; exit 2; }; APPS=("$APP") ;;
  migration) [ -z "$APP" ] || { echo '--app kan ikke bruges med migration' >&2; exit 2; }; APPS=(laser folie 3dprint scratch microbit) ;;
  *) echo 'release-set skal være base, app eller migration' >&2; exit 2 ;;
esac
[ "$(id -u)" = 0 ] || { echo 'skal køres som root' >&2; exit 2; }
[ ! -e "$WORK" ] && [ ! -L "$WORK" ] || { echo "arbejdsmappen findes allerede: $WORK" >&2; exit 2; }
SRC=$(realpath -- "$SRC"); INPUTS=$(realpath -m -- "$INPUTS"); TOOLS=$(realpath -m -- "$TOOLS")
RELEASED=$(realpath -- "$RELEASED"); WORK=$(realpath -m -- "$WORK")
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
while [ ! -f "$HERE/REPOSITORY-LAYOUT.json" ]; do
  [ "$HERE" != / ] || { echo 'repository_layout_missing' >&2; exit 2; }
  HERE=$(dirname -- "$HERE")
done
case "$WORK/" in "$SRC/"*) echo 'arbejdsmappen skal ligge uden for kilden' >&2; exit 2 ;; esac
mkdir -p "$WORK/locks" "$WORK/debian" "$WORK/report"
python3 -B "$HERE/scripts/export_build_source.py" --source-root "$SRC" \
  --output "$WORK/source" --report "$WORK/report/source.json" --apps "${APPS[@]}"
PACK="$WORK/source/system/linux-elevhub/packaging"
export PYTHONPATH="$WORK/source/system:$WORK/source"
COMMON=(--source-root "$WORK/source" --fallback-root "$WORK/source"
  --scratch "$INPUTS/scratch-gui-v15.1.1-local-projects-build"
  --matchbox "$INPUTS/matchbox/matchbox-window-manager"
  --legacy-manifest "$INPUTS/historical-public-manifest.json"
  --updater-source "$WORK/source" --shared-verifier "$WORK/source/system/app/simple_release.py"
  --minisign "$TOOLS/minisign-linux/minisign-linux/x86_64/minisign"
  --minisign-license "$TOOLS/minisign-0.12-LICENSE"
  --minisign-provenance "$TOOLS/minisign-license-provenance.json"
  --public-trust-dir "$WORK/source/deployment/keys" --vendor-built-from "$SRC")
python3 -B "$PACK/prepare_inputs.py" --components core updater matchbox "${COMMON[@]}" --output-dir "$WORK/locks"
# Matchbox is selected by the generated lock's exact identity. build_release.py
# verifies its reused .deb SHA, complete file inventory, version and dependencies.
MATCHBOX=$(python3 -c 'import json,sys;s=json.load(open(sys.argv[1]));print(s["package"]+"_"+s["version"]+"_"+s["architecture"]+".deb")' "$WORK/locks/makervaerkstedet-matchbox.json")
[ -f "$RELEASED/$MATCHBOX" ] && [ -f "$RELEASED/${MATCHBOX%.deb}.inputs.json" ] || {
  echo "præcis Matchbox-pakke og inputkvittering mangler: $MATCHBOX" >&2; exit 2;
}
cp -- "$RELEASED/$MATCHBOX" "$WORK/debian/$MATCHBOX"
cp -- "$RELEASED/${MATCHBOX%.deb}.inputs.json" "$WORK/debian/${MATCHBOX%.deb}.inputs.json"
LOCKS=("$WORK/locks/makervaerkstedet-matchbox.json" "$WORK/locks/makervaerkstedet-elev.json" "$WORK/locks/makervaerkstedet-updater.json")
for selected in "${APPS[@]}"; do
  EXTRA=()
  if [ "$selected" = scratch ]; then
    python3 -B "$PACK/prepare_inputs.py" --components scratch "${COMMON[@]}" \
      --scratch-receipt "$RELEASED/makervaerkstedet-scratch_15.1.1+local.20261104.1_all.inputs.json" \
      --output-dir "$WORK/scratch-baseline"
    EXTRA=(--scratch-lock "$WORK/scratch-baseline/makervaerkstedet-scratch.json")
  fi
  python3 -B "$PACK/prepare_app_inputs.py" --source-root "$WORK/source" --app "$selected" \
    --output-dir "$WORK/locks" --vendor-built-from "$SRC" "${EXTRA[@]}"
  APP_LOCK=$(python3 -c 'import json,sys;from pathlib import Path;s=json.load(open(Path(sys.argv[1])/"apps"/sys.argv[2]/"app.json",encoding="utf-8"));print(s["package"]+".json")' "$WORK/source" "$selected")
  LOCKS+=("$WORK/locks/$APP_LOCK")
done
python3 -B "$PACK/build_release.py" --release-set "$RELEASE_SET" --input-locks "${LOCKS[@]}" \
  --output-dir "$WORK/debian" --debian-keyring /usr/share/keyrings/debian-archive-keyring.gpg \
  --report-dir "$WORK/report"
printf '\nReleasekontrol afsluttet: %s (%s pakker)\n' "$RELEASE_SET" "${#LOCKS[@]}"
