#!/bin/bash
# Kør Ansible fra WSL med projektets ansible.cfg.
# Nødvendigt, fordi Ansible af sikkerhedshensyn ignorerer ansible.cfg i en mappe
# på et Windows-drev (/mnt/d/...), som ser ud til at være skrivbar for alle.
#
# Fra Windows:
#   wsl -d Debian -- bash "/mnt/d/Bambuslicer made easy/linux-elevhub/ansible/koer.sh" ping
#
#   ping   test om Ansible kan nå elevcomputerne
#   tjek   vis ændringer uden at udføre dem (--check --diff)
#   koer   udfør site.yml
# Ekstra argumenter sendes videre, fx:  koer.sh koer --limit vaerksted-01

set -euo pipefail
cd "$(dirname "$0")"
export ANSIBLE_CONFIG="$PWD/ansible.cfg"

handling="${1:-}"
shift || true

# En tom værtsliste (fx skolens hosts.yml før adresserne er udfyldt, eller en
# --limit uden match) ville ellers "lykkes" uden at røre en eneste maskine.
fejl=$(mktemp)
trap 'rm -f "$fejl"' EXIT
case "$handling" in
    ping) antal=$(ansible elevcomputere --list-hosts "$@" 2>"$fejl" | sed -n 's/.*hosts (\([0-9]*\)).*/\1/p' | head -1 || true) ;;
    tjek|koer) antal=$(ansible-playbook site.yml --list-hosts "$@" 2>"$fejl" | sed -n 's/.*hosts (\([0-9]*\)).*/\1/p' | head -1 || true) ;;
    *) antal=1 ;;
esac
if [ "${antal:-0}" -eq 0 ]; then
    # Fx at computerlisten ikke kunne hentes fra serveren (nøgle, known_hosts, server nede).
    cat "$fejl" >&2
    echo "FEJL: Ingen elevcomputere matcher. Kunne computerlisten ikke hentes fra serveren, står årsagen ovenfor. Med -i inventory/skole/fra-serveren.py skal computerne være tændt (set af serveren inden for 15 minutter); med hosts.yml skal adresserne være udfyldt. Ret eventuelt --limit. Intet er ændret." >&2
    exit 2
fi

case "$handling" in
    ping) ansible elevcomputere -m ping "$@" ;;
    tjek) ansible-playbook site.yml --check --diff "$@" ;;
    koer) ansible-playbook site.yml "$@" ;;
    *)
        echo "Brug: koer.sh ping|tjek|koer [ekstra ansible-argumenter]" >&2
        exit 1
        ;;
esac
