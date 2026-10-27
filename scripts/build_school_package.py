"""Build a neutral, credential-free Windows school setup package."""
from __future__ import annotations
import argparse, ast, hashlib, json, shutil, subprocess, sys, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'windows-elevhub'
SETUP = ROOT / 'deployment' / 'school-setup'
VERSION = '0.1.0-school.20261013'

SETUP_FILES = ('Setup-School.ps1', 'Choose-Setup.ps1', 'school-layout.ps1', 'Start.cmd', 'Start-LaserStation.ps1',
               'Open-SetupWizard.ps1', 'Invoke-SetupWizard.ps1', 'Setup-Wizard.xaml', 'setup-wizard-view.ps1',
               'setup-wizard-model.ps1', 'setup-wizard-runner.ps1', 'setup-wizard-postflight.ps1',
               'Setup-LaserStation.ps1', 'Install-LaserStation.cmd', 'Update-LaserStation.cmd', 'Open-LaserStation.ps1',
               'Stop-LaserStation.ps1', 'Stop-LaserStation.cmd',
               'Manage-School.ps1', 'Manage-School.cmd', 'Open-ControlPanel.cmd', 'Open-ControlPanel.ps1',
               'Remove-School.cmd', 'Remove-School.ps1', 'Maintain-School.cmd', 'Maintain-School.ps1',
               'Join-Release.ps1', 'README.md', 'enrollment.py')


def copy_setup_entrypoints(output: Path):
    """Stage both deployment choices before the complete package is sealed."""
    target = output / 'deployment' / 'school-setup'
    target.mkdir(parents=True)
    for name in SETUP_FILES:
        shutil.copy2(SETUP / name, target / name)
    for public, entry in (('START', 'Start'), ('Manage-School', 'Manage-School'),
                          ('Maintain-School', 'Maintain-School'), ('Remove-School', 'Remove-School'),
                          ('Install-LaserStation', 'Install-LaserStation'), ('Update-LaserStation', 'Update-LaserStation'),
                          ('Stop-LaserStation', 'Stop-LaserStation')):
        (output / (public + '.cmd')).write_text(
            '@echo off\r\ncall "%~dp0deployment\\school-setup\\' + entry + '.cmd" %*\r\n', encoding='ascii')

def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def neutralize_teacher_code(path: Path):
    """Accept current code; still neutralize old payloads during migration."""
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(path))
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'TEACHER_CODE' for target in node.targets)]
    if not assignments:
        return
    if len(assignments) != 1:
        raise ValueError('Forventede præcis én TEACHER_CODE-assignment i skolepakken.')
    line = source.splitlines(keepends=True)[assignments[0].lineno - 1]
    if 'TEACHER_CODE' not in line or '=' not in line:
        raise ValueError('Kunne ikke neutralisere lærer-kode assignment.')
    source_lines = source.splitlines(keepends=True)
    source_lines[assignments[0].lineno - 1] = line[:line.index('TEACHER_CODE')] + 'TEACHER_CODE = None\n'
    path.write_text(''.join(source_lines), encoding='utf-8')
    if 'TEACHER_CODE = None' not in path.read_text(encoding='utf-8'):
        raise ValueError('Lærer-koden blev ikke neutraliseret.')

def write_release_manifest(output: Path, release: str):
    """Seal the complete program after its transport integrity manifest.

    The transport manifest excludes this one file to avoid a hash cycle. This
    manifest includes the transport manifest, and generation installs bind its
    digest separately in their ownership marker.
    """
    from app.release_compatibility import CREDIT, inspect_program
    files = {path.relative_to(output).as_posix(): {'sha256': digest(path), 'size': path.stat().st_size}
             for path in sorted(output.rglob('*'))
             if path.is_file() and path.name != 'makerspace-release.json'}
    value = {'format': 1, 'product': 'Makerværkstedet', 'release': release, 'credits': CREDIT,
             'data_contract': {'hub': 1, 'print': 1, 'laser': 1}, 'files': files}
    (output / 'makerspace-release.json').write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return inspect_program(output)


def build(output: Path, role: str, school_name: str, theme: str, archive: bool = False,
          generation_layout: bool = False,
          artifact_manifest: Path | None = None):
    if role not in {'vm', 'makerspace'} or theme not in {'red', 'yellow', 'green'}:
        raise ValueError('role/theme er ugyldig')
    # Validate external inputs before creating an output directory.
    sys.path.insert(0, str(SOURCE))
    import build_packages
    verified_artifacts = build_packages.load_artifact_manifest(artifact_manifest)
    required = ['server.tools', 'server.licenses']
    build_packages.require_artifacts(verified_artifacts, required, output)
    build_packages.validate_build_inputs(verified_artifacts, 'server')
    if output.exists():
        raise ValueError('output findes allerede; vælg en ny mappe')
    output.mkdir(parents=True)
    # Reuse the tested server payload, which contains the application/web backend
    # and runtime dependencies, while keeping its security/data directories out.
    build_packages.build_server(output,
                                artifact_manifest=artifact_manifest)
    # A generic school installation uses its own name/theme and the fixed
    # textual credit. School identity images are not public build inputs.
    for relative in ('web/assets/klostermarksskolen-logo.png',
                     'hub/web/assets/klostermarksskolen-logo.png'):
        logo = output / relative
        build_packages._assert_no_reparse_ancestors(logo)
        if logo.exists():
            if not logo.resolve().is_relative_to(output.resolve()):
                raise ValueError('Skolelogoets sti ligger uden for pakkens stagingmappe.')
            logo.unlink()
    if generation_layout:
        (output / 'runtime/python314._pth').write_text(
            'python314.zip\n.\n..\nLib/site-packages\n', encoding='ascii')
    neutralize_teacher_code(output / 'app' / 'server.py')
    build_packages.manifest(output, 'server', artifact_manifest=artifact_manifest)
    # Geometry and runtime dependencies come from the same verified wheels
    # as the server, never from the builder machine's installed distributions.
    # Remove the pilot README, which contains site-specific historical
    # addresses. The distributable carries only neutral setup instructions.
    (output / 'server' / 'README.md').unlink(missing_ok=True)
    for stale in ('INSTALLATION-VIA-SSH.md', 'LAES-MIG.md', 'Fjern server.cmd', 'Tilmeld elevcomputer.cmd', 'ElevhubServerSetup.exe'):
        (output / stale).unlink(missing_ok=True)
    # The root licence and each component notice are copied by the neutral
    # documentation generator; the Elevhub MIT component remains separate.
    from scripts.package_documentation import copy_documentation
    # Let the guide renderer resolve the actual installer README and commands.
    copy_setup_entrypoints(output)
    copy_documentation(output)
    migration_note = ROOT / 'deployment' / 'school-setup' / 'LAES-MIG-FOER-INSTALLATION.txt'
    if migration_note.is_file(): shutil.copy2(migration_note, output / migration_note.name)
    package_marker = json.loads((output / 'elevhub-package.json').read_text(encoding='utf-8'))
    # The server verifier requires its canonical build target; installation
    # rewrites this to the chosen RoleIp and regenerates TLS.
    package_marker['server'] = None
    package_marker['requiresSchoolSetup'] = True
    package_marker['containsCredentials'] = False
    package_marker['version'] = VERSION
    package_marker['requiresGenerationLayout'] = generation_layout
    (output / 'elevhub-package.json').write_text(json.dumps(package_marker, indent=2) + '\n', encoding='utf-8')
    (output / 'school-package.json').write_text(json.dumps({
        'schemaVersion': 1, 'format': 'elevhub-school-windows-v1', 'version': VERSION,
        'role': role, 'school_name': school_name, 'theme': theme,
        'containsCredentials': False, 'containsPrinterData': False,
        'containsJobs': False, 'privateKeyGeneratedOnInstall': True,
        'enrollmentPort': 8774, 'studentSubnetRequired': True,
        'deploymentChoices': ['single-computer', 'separate-computers'],
        'separateLaserStation': {'connectionFile': 'laser-station-connection.json', 'transport': 'https'},
        'bedReleaseDefault': 'teacher_manual', 'cameraAutoEnabledByDefault': False,
        'requiresGenerationLayout': generation_layout,
        'releaseStatus': 'pilot-requires-hardware-installation-test',
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # The setup scripts are added after the base server manifest was created.
    # Normalize them too before hashing, for Danish text in Windows PowerShell 5.1.
    for script in output.rglob('*.ps1'):
        script.write_text(script.read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
    files = {p.relative_to(output).as_posix(): digest(p) for p in output.rglob('*') if p.is_file() and p.name != 'integrity.json'}
    (output / 'integrity.json').write_text(json.dumps({'algorithm':'sha256','files':files}, indent=2) + '\n', encoding='utf-8')
    release = write_release_manifest(output, VERSION)
    result = {'folder': str(output), 'files': len(files) + 1, 'credentialFree': True,
              'manifest_sha256': release['manifest_sha256']}
    if archive:
        target_zip = output.with_suffix('.zip')
        with zipfile.ZipFile(target_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
            for path in output.rglob('*'):
                if path.is_file(): bundle.write(path, output.name + '/' + path.relative_to(output).as_posix())
        result['zip'] = str(target_zip); result['sha256'] = digest(target_zip)
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--artifact-manifest', type=Path, required=True,
                        help='JSON-manifest med version og SHA-256 for eksterne public artifacts')
    parser.add_argument('--role', choices=('vm','makerspace'), required=True)
    parser.add_argument('--school-name', default='')
    parser.add_argument('--theme', choices=('red','yellow','green'), default='green')
    parser.add_argument('--zip', action='store_true')
    parser.add_argument('--generation-layout', action='store_true',
                        help='Build for atomic program/data generations and the maintenance operator.')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    print(json.dumps(build(args.output.resolve(), args.role, args.school_name, args.theme, args.zip,
                           generation_layout=args.generation_layout,
                           artifact_manifest=args.artifact_manifest), ensure_ascii=False))
