"""Map source guides to the installed layout without publishing dangling links."""
from pathlib import Path
import re
import shutil
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
GUIDES = ('KOM-GODT-IGANG.md', 'TO-OPSAETNINGER.md', 'AGENTINDGANG.md', 'SKOLETILPASNING.md', 'MAKERSPACE-ARKITEKTUR.md',
          'RELEASE-LICENSER.md', 'GITHUB-OPDATERINGER.md', 'OPDATERINGSKOE.md',
          'PRINTERFRIGIVELSE.md', 'SERVERKONTROLPANEL.md', 'MAKERSPACE-BACKUP.md',
          'MAKERSPACE-OPDATERING.md', 'INSTALLATIONSREPARATION.md')
LINK = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
CLIENT_README = '''# Historisk Windows-elevklient

Denne mappe er bevaret som historisk research og kildekode. Windows-
elevklienten er ikke en understøttet produktplatform og må ikke installeres
eller annonceres som en del af en skoleudgivelse. Makerværkstedets eneste
understøttede elevcomputerplatform er Linux; Windows bruges som lærerens
server- og administrationsplatform.

Den historiske implementation indeholder en lokal shell, webværksteder,
projekthylden og de installerede Scratch-, Datatræner-, Kredsløb- og CaDoodle-
komponenter. Den tekst beskriver ikke en aktuel support- eller
installationskontrakt.

Historiske administratorhandlinger ligger i `client/admin/`.
Læs den native klients kontrakt i [client/native/README.md](client/native/README.md)
og projekthyldens vejledning i [projekter/README.md](projekter/README.md).

Kilde- og tilpasningsvejledningen til en kodeagent er
[docs/AGENTINDGANG.md](docs/AGENTINDGANG.md). Den beskriver skolekildens
arkitektur og grænser; den installerede klient har ingen særskilt agentlogin
eller agentstyring.

Projektets egne dele følger [GPL-3.0-or-later](LICENSE) og
[licensafgrænsningen](LICENSE-SCOPE.md). Elevhubs MIT-komponent har sin
[egen notice](licenses/Elevhub-LICENSE.txt), og øvrige komponenter beholder
deres egne licenser.

**Kreditering: Klostermarksskolen.**
'''


def _map(relative):
    if relative == 'linux-elevhub/LICENSE':
        return 'licenses/Elevhub-LICENSE.txt'
    if relative in {'web/assets/CC-BY-4.0.txt', 'web/assets/PUBLIC-ILLUSTRATIONS.md'}:
        return 'licenses/' + Path(relative).name
    for source, target in (('linux-elevhub/server/elevhub/', 'hub/'),
                           ('linux-elevhub/projekter/', 'projekter/'),
                           ('linux-elevhub/cadoodle/kloster/', 'licenses/CaDoodle-overlay-source/'),
                           ('windows-elevhub/server/', 'server/'),
                           ('windows-elevhub/client/native/', 'client/native/'),
                           ('windows-elevhub/client/cadoodle/', 'native-apps/cadoodle/')):
        if relative.startswith(source):
            return target + relative[len(source):]
    return relative


# These source trees are copied by the Windows builders.  Render only our
# own documents after the package tree exists; upstream artifact documents
# under apps/ and native-apps/ are deliberately left untouched.
INSTALLED_MARKDOWN_ROOTS = (
    ('web/assets/', 'web/assets/'),
    ('windows-elevhub/server/', 'server/'),
    ('windows-elevhub/client/native/', 'client/native/'),
    ('linux-elevhub/server/elevhub/', 'hub/'),
    ('linux-elevhub/projekter/', 'projekter/'),
    ('linux-elevhub/cadoodle/kloster/', 'licenses/CaDoodle-overlay-source/'),
)


def render(text, source_relative, output, *, output_relative=None):
    """Keep real installed links; name unbundled source as source-only references."""
    from os.path import relpath
    source_parent = (ROOT / source_relative).parent
    installed_parent = (output / (output_relative or source_relative)).parent

    def rewrite(match):
        label, target = match.groups()
        parsed = urlsplit(target.strip().strip('<>'))
        if parsed.scheme or target.startswith('//') or not parsed.path:
            return match.group(0)
        lexical = (source_parent / unquote(parsed.path)).resolve()
        if not lexical.is_relative_to(ROOT):
            return label + ' (lokalt arkiv, ikke med i pakken)'
        relative = lexical.relative_to(ROOT).as_posix()
        installed = output / _map(relative)
        if installed.exists():
            link = Path(relpath(installed, installed_parent)).as_posix()
            if parsed.fragment:
                link += '#' + parsed.fragment
            return '[' + label + '](' + link + ')'
        return label + ' (i tilsvarende kildeudgivelse: `' + relative + '`)'

    return LINK.sub(rewrite, text)


def render_installed_markdown(output):
    """Rewrite links in copied first-party Markdown without changing sources."""
    output = Path(output)
    for source_root, output_root in INSTALLED_MARKDOWN_ROOTS:
        source_base = ROOT / source_root
        output_base = output / output_root
        if not source_base.is_dir() or not output_base.is_dir():
            continue
        for destination in output_base.rglob('*.md'):
            if not destination.is_file():
                continue
            relative = destination.relative_to(output_base)
            source = source_base / relative
            if not source.is_file():
                continue
            destination.write_text(
                render(source.read_text(encoding='utf-8'),
                       (Path(source_root) / relative).as_posix(), output,
                       output_relative=(Path(output_root) / relative).as_posix()),
                encoding='utf-8')


def render_installed_readmes(output):
    """Backward-compatible name for the installed first-party Markdown pass."""
    render_installed_markdown(output)


def copy_client_documentation(output):
    """Create the neutral client documentation and its license notices."""
    output = Path(output)
    (output / 'docs').mkdir(parents=True, exist_ok=True)
    (output / 'licenses').mkdir(parents=True, exist_ok=True)
    for relative in ('LICENSE', 'LICENSE-SCOPE.md', 'CREDITS.md'):
        shutil.copyfile(ROOT / relative, output / relative)
    shutil.copyfile(ROOT / 'linux-elevhub/LICENSE', output / 'licenses/Elevhub-LICENSE.txt')
    copy_illustration_notices(output)
    shutil.copyfile(ROOT / 'docs/AGENTINDGANG.md', output / 'docs/AGENTINDGANG.md')
    (output / 'AGENTS.md').write_text(
        '# Kodeagent på en installeret Makerværksted-elevklient\n\n'
        '**Kreditering: Klostermarksskolen.**\n\n'
        'Brug [agentindgangen](docs/AGENTINDGANG.md) som kilde- og '
        'skoletilpasningsreference. Læreren styrer server, maskiner og workflows; '
        'klienten har ingen særskilt agentstyring.\n', encoding='utf-8')
    (output / 'README.md').write_text(CLIENT_README, encoding='utf-8')
    # These destinations exist before resolving links in the neutral documents.
    for relative in ('CREDITS.md', 'LICENSE-SCOPE.md', 'docs/AGENTINDGANG.md', 'README.md'):
        path = output / relative
        path.write_text(render(path.read_text(encoding='utf-8'), relative, output), encoding='utf-8')
    render_installed_markdown(output)


def copy_documentation(output):
    output = Path(output)
    copy_illustration_notices(output)
    (output / 'docs').mkdir(parents=True, exist_ok=True)
    copies = ['CREDITS.md', 'LICENSE', 'LICENSE-SCOPE.md'] + ['docs/' + name for name in GUIDES]
    for relative in copies:
        shutil.copyfile(ROOT / relative, output / relative)
    shutil.copyfile(ROOT / 'deployment/PACKAGE-README.md', output / 'README.md')
    (output / 'AGENTS.md').write_text(
        '# Kodeagent på en installeret Makerværksted-server\n\n'
        '**Kreditering: Klostermarksskolen.**\n\n'
        'Start i [agentindgangen](docs/AGENTINDGANG.md) og '
        '[skoletilpasning](docs/SKOLETILPASNING.md). Læreren styrer systemet.\n\n'
        'Programkode ligger i `app/`, `web/`, `hub/` og `server/`. '
        'Driftsdata og nøgler ligger særskilt i installationens datamappe. '
        'En ændring af forseglet kode bryder versionskontrollen; hent den '
        'tilsvarende kildeudgivelse og brug dens bygge- og testvejledning. '
        'Sikkerhed, jobhistorik, fysisk kontrol og eksisterende elevfiler skal bevares. '
        'Daglig agentstyring, agentnøgler og agentpanel skal ikke tilføjes.\n', encoding='utf-8')
    # All destinations exist before resolving cross-references.
    for relative in copies:
        if relative.endswith('.md'):
            (output / relative).write_text(render((ROOT / relative).read_text(encoding='utf-8'), relative, output), encoding='utf-8')
    (output / 'README.md').write_text(render((ROOT / 'deployment/PACKAGE-README.md').read_text(encoding='utf-8'),
                                            'deployment/PACKAGE-README.md', output, output_relative='README.md'), encoding='utf-8')
    render_installed_markdown(output)


def copy_illustration_notices(output):
    output = Path(output)
    (output / 'licenses').mkdir(parents=True, exist_ok=True)
    for name in ('CC-BY-4.0.txt', 'PUBLIC-ILLUSTRATIONS.md'):
        shutil.copyfile(ROOT / 'web/assets' / name, output / 'licenses' / name)
    notice = output / 'licenses/PUBLIC-ILLUSTRATIONS.md'
    notice.write_text(render(notice.read_text(encoding='utf-8'),
                             'web/assets/PUBLIC-ILLUSTRATIONS.md', output,
                             output_relative='licenses/PUBLIC-ILLUSTRATIONS.md'), encoding='utf-8')
