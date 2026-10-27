#!/bin/sh
# Open the local admin screen only after a physical tty2 login.
if [ ! -t 0 ] || [ "$(tty 2>/dev/null)" != /dev/tty2 ] || [ -n "${SSH_CONNECTION:-}" ] || [ -n "${DISPLAY:-}" ]; then
    exit 0
fi

if command -v startx >/dev/null 2>&1 && [ -x /usr/local/bin/elevhub-admin-xsession ]; then
    startx /usr/local/bin/elevhub-admin-xsession -- -nolisten tcp
    sudo -n /usr/local/sbin/elevhub-admin-return
    exit 0
fi

printf '\nGrafisk adminflade er ikke tilgængelig. Du er fortsat logget ind i den lokale terminal.\n'
exit 1
