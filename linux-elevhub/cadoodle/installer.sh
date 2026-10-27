#!/bin/sh
# Kør på Debian amd64 som administrator; installationsmappen skal også have
# elevhub-cadoodle, klargoer.py og kiosk/files/cadoodle_vaerksted.py.
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Kør med sudo.' >&2; exit 1; }
[ "$(dpkg --print-architecture)" = amd64 ] || { echo 'Kræver amd64.' >&2; exit 1; }
folder=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for file in elevhub-cadoodle klargoer.py cadoodle_vaerksted.py; do
    test -f "$folder/$file" || { echo "Mangler $file" >&2; exit 1; }
done
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends wget ca-certificates libgtk-3-0t64 libgl1-mesa-dri
install -d -m 755 /usr/share/desktop-directories /var/cache/elevhub
package=/var/cache/elevhub/CaDoodle-1.9.6-amd64.deb
checksum=6ee1ed049127a25a07331e8a9f61035e98130310cb7991f48bf9fcfb0880520a
if ! printf '%s  %s\n' "$checksum" "$package" | sha256sum -c -; then
    wget -O "$package.part" https://github.com/CommonWealthRobotics/CaDoodle/releases/download/1.9.6/CaDoodle-Linux-x86_64.deb
    printf '%s  %s\n' "$checksum" "$package.part" | sha256sum -c -
    mv "$package.part" "$package"
fi
apt-get install -y "$package"
install -d -o elev -g elev /home/elev/bin/CaDoodle-ApplicationInstall
touch /home/elev/bin/CaDoodle-ApplicationInstall/pinVersion
chown elev:elev /home/elev/bin/CaDoodle-ApplicationInstall/pinVersion
python3 "$folder/klargoer.py" "$folder"
echo 'Start CaDoodle én gang som elev, så de medfølgende ressourcer pakkes ud.'
echo 'Kør derefter klargoer.py igen, så projekterne lægges i Projekter/3D-print.'
