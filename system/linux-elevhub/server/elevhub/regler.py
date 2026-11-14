"""Hvad hver elevindgang lukker igennem til værkstedet bag den.

En regel får metode og rå sti og svarer med en Afgoerelse:
    ('tillad', None)          send videre
    ('omdiriger', '/sti')     send eleven til en anden side i samme app
    ('afvis', None)           403, når aldrig værkstedet

Værkstederne har deres egne kontroller. Reglerne her er det ekstra lag, der sikrer,
at elevcomputerne kun når elevdelen af den app, de er på.
"""
from urllib.parse import unquote, urlsplit

TILLAD = ('tillad', None)
AFVIS = ('afvis', None)


def ren_sti(raa_sti):
    """Afkodet sti, eller None hvis den prøver at snyde (backslash, .., nul-tegn)."""
    sti = unquote(urlsplit(raa_sti).path)
    if '\\' in sti or '..' in sti or '\x00' in sti or not sti.startswith('/'):
        return None
    return sti


def printvaerksted(metode, raa_sti):
    """Printværkstedet (127.0.0.1:8765): siden, køstatus og upload. Aldrig lærersiden."""
    sti = ren_sti(raa_sti)
    if sti is None:
        return AFVIS
    lav = sti.lower()
    if metode in ('GET', 'HEAD'):
        if sti in ('/', '/api/state', '/api/health', '/api/school-appearance'):
            return TILLAD
        if lav.startswith('/api/') or 'laerer' in lav or 'teacher' in lav or lav.startswith('/laser') \
                or lav in ('/workshop.html', '/folie.html'):
            return AFVIS
        return TILLAD
    if metode == 'POST' and sti == '/api/jobs':
        return TILLAD
    return AFVIS


def _designvaerksted(metode, raa_sti, maa_aflevere):
    """Design entrances carry queue traffic only.

    The laser and foil editors run on the pupil computer. The school server
    does not serve their pages, scripts or server-side drawing helpers: with
    hundreds of pupil computers the server must only handle updates,
    administration and queues. The fixed library routes are handled before
    these rules by the entrance itself.
    """
    sti = ren_sti(raa_sti)
    if sti is None:
        return AFVIS
    if metode in ('GET', 'HEAD'):
        return TILLAD if sti in ('/api/laser/health', '/api/school-appearance') else AFVIS
    if metode == 'POST' and maa_aflevere and sti == '/api/laser/submit':
        return TILLAD
    return AFVIS


def laser(metode, raa_sti):
    """Laserindgangen (127.0.0.1:8766): kun aflevering til læreren og helbred."""
    return _designvaerksted(metode, raa_sti, maa_aflevere=True)


def folie(metode, raa_sti):
    """Folieindgangen (127.0.0.1:8766): kun helbred. Ingen aflevering."""
    return _designvaerksted(metode, raa_sti, maa_aflevere=False)


REGLER = {'printvaerksted': printvaerksted, 'laser': laser, 'folie': folie}
