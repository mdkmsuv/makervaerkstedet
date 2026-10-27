"""TLS og enhedsidentitet. Hemmeligheder bliver aldrig returneret eller logget."""
import hashlib
import hmac
import ipaddress
import json
import re
import ssl
from pathlib import Path

NAVN = re.compile(r'[a-z0-9][a-z0-9-]{0,31}')
HASH = re.compile(r'[0-9a-f]{64}')
TOKEN = re.compile(r'[A-Za-z0-9_-]{43,128}')


class Enhedsregister:
    """Læs register ved hvert login: rotation/spærring gælder uden genstart."""

    def __init__(self, sti):
        self.sti = Path(sti)
        self._laes()  # Defekt register stopper opstarten.

    def _laes(self):
        if self.sti.stat().st_size > 1024 * 1024:
            raise ValueError('Enhedsregisteret er for stort.')
        data = json.loads(self.sti.read_text(encoding='utf-8'))
        enheder = data.get('enheder') if isinstance(data, dict) else None
        if not isinstance(enheder, dict) or len(enheder) > 256:
            raise ValueError('Ugyldigt enhedsregister.')
        brugte = set()
        for navn, noegler in enheder.items():
            if not NAVN.fullmatch(navn) or not isinstance(noegler, dict):
                raise ValueError('Ugyldig enhed i registeret.')
            for felt in ('token_sha256', 'ui_token_sha256'):
                vaerdi = noegler.get(felt)
                if not isinstance(vaerdi, str) or not HASH.fullmatch(vaerdi) or vaerdi in brugte:
                    raise ValueError('Ugyldig eller genbrugt enhedsnøgle.')
                brugte.add(vaerdi)
        return enheder

    def godkend(self, navn, autorisation, rolle):
        if rolle not in ('root', 'ui'):
            return False
        if not isinstance(navn, str) or not NAVN.fullmatch(navn):
            return False
        if not isinstance(autorisation, str) or not autorisation.startswith('Bearer '):
            return False
        token = autorisation[7:]
        if not TOKEN.fullmatch(token):
            return False
        try:
            noegler = self._laes().get(navn, {})
        except (OSError, ValueError, TypeError):
            return False  # Ingen adgang med et manglende/defekt register.
        felt = 'token_sha256' if rolle == 'root' else 'ui_token_sha256'
        forventet = noegler.get(felt, '')
        return hmac.compare_digest(hashlib.sha256(token.encode('ascii')).hexdigest(), forventet)

    def inventar(self):
        """Aktuelle registrerede navne; aldrig nøgler eller registerets rå indhold."""
        try:
            return sorted(self._laes())
        except (OSError, ValueError, TypeError):
            return None


def lokal_backend(tekst):
    """Backend skal blive på serveren; netværksstrækningen beskyttes af TLS."""
    vaert, _, port = tekst.rpartition(':')
    try:
        return ipaddress.ip_address(vaert).is_loopback and 1 <= int(port) <= 65535
    except ValueError:
        return False


def tls_kontekst(konf, basis):
    """Ingen automatisk nedgradering. HTTP findes kun som eksplicit testtilstand."""
    usikker = konf.get('usikker_test', False)
    if type(usikker) is not bool:
        raise ValueError('usikker_test skal være true eller false.')
    tls = konf.get('tls')
    if usikker:
        if tls:
            raise ValueError('Vælg enten TLS eller eksplicit usikker test.')
        return None
    if not isinstance(tls, dict) or not tls.get('certifikat') or not tls.get('noegle'):
        raise ValueError('HTTPS-certifikat og nøgle kræves. HTTP må kun bruges med usikker_test i et isoleret testmiljø.')
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(Path(basis) / tls['certifikat']), str(Path(basis) / tls['noegle']))
    return ctx
