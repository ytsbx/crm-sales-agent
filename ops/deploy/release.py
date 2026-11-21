#!/usr/bin/env python3
"""Build a single-commit release; check/apply it on the existing CRM host.

Standard library only. See RELEASE.md. Production is never changed without --apply.
"""
import argparse
import ast
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request


ROOT = Path('/opt/crm-sales-agent')
SERVICE = 'crm-sales.service'
PUBLIC = 'https://crm.mandla.cn'
HEALTH = 'http://127.0.0.1:8010/health'
GUARDS = ('backend/requirements.txt', 'backend/alembic.ini', 'backend/alembic')
RECORDS = ('DEPLOYED_COMMIT', 'WEB_DEPLOYED_COMMIT', 'RELEASE.json')


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(root):
    result = {}
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise ValueError(f'Symlink refused: {p}')
        if '__pycache__' in p.parts or p.suffix == '.pyc':
            continue
        if p.is_file():
            result[p.relative_to(root).as_posix()] = digest(p)
    return result


def valid_path(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or str(p) != name:
        raise ValueError(f'Unsafe archive path: {name!r}')


def unpack(archive, destination):
    # Reject links/devices/duplicates before extracting anything. Never extractall.
    with tarfile.open(archive) as tar:
        seen = set()
        members = tar.getmembers()
        for member in members:
            valid_path(member.name)
            if '__pycache__' in PurePosixPath(member.name).parts or member.name.endswith('.pyc'):
                raise ValueError(f'Bytecode refused: {member.name}')
            if member.name in seen or not (member.isfile() or member.isdir()):
                raise ValueError(f'Unsafe archive member: {member.name}')
            seen.add(member.name)
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open('wb') as out:
                    shutil.copyfileobj(source, out)
                target.chmod(0o644)


def snapshot(repo, ref, target):
    commit = run('git', '-C', str(repo), 'rev-parse', '--verify', ref + '^{commit}')
    archive = target.parent / (target.name + '.tar')
    subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar',
                    '--output', str(archive), commit], check=True)
    target.mkdir()
    unpack(archive, target)
    return commit


def subset(root, paths):
    out = {}
    for name in paths:
        path = root / name
        if path.is_dir():
            out.update({name + '/' + k: v for k, v in files(path).items()})
        else:
            out[name] = digest(path)
    return out


def build(args):
    repo = Path(run('git', 'rev-parse', '--show-toplevel'))
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError('Output already exists; choose a new filename')
    # Worktree changes cannot accidentally be included: all inputs come from git archive.
    with tempfile.TemporaryDirectory(prefix='crm-build-') as tmp:
        base = Path(tmp)
        source, baseline, package = base / 'source', base / 'baseline', base / 'package'
        sha = snapshot(repo, args.ref, source)
        old_sha = snapshot(repo, args.baseline, baseline)
        if subset(source, GUARDS) != subset(baseline, GUARDS):
            raise ValueError('Dependency/migration/config files changed; separate review required')
        install_args = ['pnpm', 'install', '--frozen-lockfile']
        if not args.allow_downloads:
            install_args.append('--offline')
        subprocess.run(install_args,
                       cwd=source / 'frontend', check=True)
        subprocess.run(['pnpm', 'run', 'build'], cwd=source / 'frontend', check=True)
        package.mkdir()
        shutil.copytree(source / 'backend/app', package / 'backend/app')
        for name in GUARDS:
            src, dst = source / name, package / name
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        shutil.copytree(source / 'frontend/dist', package / 'web')
        manifest = {
            'schema': 1, 'commit': sha, 'baseline_commit': old_sha,
            'built_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'files': files(package), 'baseline_app': files(baseline / 'backend/app'),
            'guards': subset(baseline, GUARDS),
        }
        (package / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        # mode=x prevents overwriting an existing release.
        with tarfile.open(output, 'x:gz') as tar:
            for p in sorted(package.rglob('*')):
                tar.add(p, arcname=p.relative_to(package).as_posix(), recursive=False)
    print(json.dumps({'bundle': str(output), 'commit': sha, 'sha256': digest(output)}))


def verify_package(package):
    manifest = json.loads((package / 'manifest.json').read_text())
    if manifest.get('schema') != 1:
        raise ValueError('Unsupported manifest schema')
    for key in ('commit', 'baseline_commit'):
        if not re.fullmatch('[0-9a-f]{40}', manifest[key]):
            raise ValueError('Invalid commit')
    actual = files(package)
    actual.pop('manifest.json')
    if actual != manifest['files']:
        raise ValueError('Package file hash/set mismatch')
    allowed = ('backend/app/', 'backend/alembic/', 'web/')
    for name in actual:
        if name not in GUARDS and not name.startswith(allowed):
            raise ValueError(f'Unexpected payload: {name}')
        if Path(name).name.startswith('.env') or Path(name).name.startswith('._'):
            raise ValueError(f'Forbidden payload: {name}')
    for required in ('backend/app/main.py', 'web/index.html', 'backend/requirements.txt'):
        if required not in actual:
            raise ValueError(f'Missing required file: {required}')
    for p in (package / 'backend/app').rglob('*.py'):
        ast.parse(p.read_bytes(), filename=str(p))
    if subset(package, GUARDS) != manifest['guards']:
        raise ValueError('Package dependency/migration guards differ')
    return manifest


def fetch(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read()


class Host:
    def __init__(self, root=ROOT):
        self.root = root
        self.app = root / 'app'

    def control(self, action):
        subprocess.run(['systemctl', action, SERVICE], check=True, timeout=60)

    def layout(self):
        workdir = run('systemctl', 'show', SERVICE, '-p', 'WorkingDirectory', '--value')
        command = run('systemctl', 'show', SERVICE, '-p', 'ExecStart', '--value')
        if workdir != str(self.app / 'backend') or str(self.app / 'backend/.venv/bin/') not in command:
            raise ValueError('Unexpected systemd runtime layout')
        if '--port 8010' not in command or '--workers 1' not in command:
            raise ValueError('Unexpected backend port/worker settings')
        nginx = subprocess.check_output(['nginx', '-T'], stderr=subprocess.STDOUT, text=True)
        if not re.search(r'root\s+' + re.escape(str(self.app / 'web-dist')) + r'\s*;', nginx):
            raise ValueError('Expected nginx root not found')
        if run('systemctl', 'is-active', SERVICE) != 'active':
            raise ValueError('Backend is not active before release')
        python = str(self.app / 'backend/.venv/bin/python')
        current = run(python, '-m', 'alembic', 'current', cwd=self.app / 'backend')
        heads = run(python, '-m', 'alembic', 'heads', cwd=self.app / 'backend')
        pattern = r'(?m)^([a-zA-Z0-9_]+)\s+\(head\)'
        if not re.findall(pattern, current) or set(re.findall(pattern, current)) != set(re.findall(pattern, heads)):
            raise ValueError('Database is not at installed migration head; separate review required')

    def healthy(self):
        try:
            data = json.loads(fetch(HEALTH))['data']
            return data['status'] == 'up' and data['database'] == 'ok'
        except (ValueError, KeyError, OSError):
            return False

    def wait_health(self):
        for _ in range(30):
            try:
                active = run('systemctl', 'is-active', SERVICE) == 'active'
            except subprocess.CalledProcessError:
                active = False
            if active and self.healthy():
                return
            time.sleep(1)
        raise RuntimeError('Backend health/database check failed')

    def public(self, web):
        index = (web / 'index.html').read_bytes()
        if fetch(PUBLIC + '/?release_check=' + str(time.time_ns())) != index:
            raise ValueError('Public index differs')
        # Check directly referenced entry scripts/styles/favicon, not SPA /health fallback.
        for path in set(re.findall(r'(?:src|href)=["\'](/[^"\']+)["\']', index.decode())):
            relative = path.split('?', 1)[0].lstrip('/')
            valid_path(relative)
            local = web / relative
            if not local.is_file() or fetch(PUBLIC + path) != local.read_bytes():
                raise ValueError(f'Public entry resource differs: {path}')


def preflight(package, manifest, host):
    host.layout()
    for path in (host.app, host.app / 'backend', host.app / 'backend/app', host.app / 'web-dist'):
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f'Unexpected/symlinked live path: {path}')
    if (host.root / 'backup').is_symlink():
        raise ValueError('Symlinked backup directory refused')
    if files(host.app / 'backend/app') != manifest['baseline_app']:
        raise ValueError('Live backend differs from baseline; refusing unknown overwrite')
    files(host.app / 'web-dist')  # Reject links anywhere in the overlay destination.
    if subset(host.app, GUARDS) != manifest['guards']:
        raise ValueError('Live dependency/migration/config differs; separate review required')
    for name in RECORDS:
        p = host.app / name
        if p.is_symlink() or (p.exists() and not p.is_file()):
            raise ValueError(f'Unexpected record path: {p}')
    if not host.healthy():
        raise ValueError('Backend/database unhealthy before release')
    # Budget for payload, backup and failed tree retained during rollback.
    size = sum(p.stat().st_size for tree in (package, host.app / 'backend/app', host.app / 'web-dist')
               for p in tree.rglob('*') if p.is_file())
    if shutil.disk_usage(host.root).free < size * 3 + 128 * 1024 * 1024:
        raise ValueError('Insufficient disk space for safe backup/rollback')


def atomic_write(path, data):
    pending = path.with_name(path.name + '.release-tmp')
    # exclusive open also refuses an unexpected pre-existing file/link.
    out = pending.open('xb')
    try:
        with out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        pending.chmod(0o644)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def verify_live(package, manifest, host):
    if files(host.app / 'backend/app') != files(package / 'backend/app'):
        raise ValueError('Installed backend content differs')
    for name, sha in manifest['files'].items():
        target = host.app / ('web-dist/' + name[4:] if name.startswith('web/') else name)
        if target.is_symlink() or digest(target) != sha:
            raise ValueError(f'Installed content differs: {name}')
    host.wait_health()
    host.public(package / 'web')


def install(package, manifest, host):
    backups = host.root / 'backup'
    backups.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='unified-' + manifest['commit'][:12] + '-', dir=backups))
    shutil.copytree(host.app / 'backend/app', backup / 'backend-app')
    shutil.copytree(host.app / 'web-dist', backup / 'web')
    if files(backup / 'backend-app') != manifest['baseline_app'] or files(host.app / 'backend/app') != manifest['baseline_app']:
        raise ValueError(f'Backend changed during backup; refusing release; backup={backup}')
    if files(backup / 'web') != files(host.app / 'web-dist'):
        raise ValueError(f'Frontend changed during backup; refusing release; backup={backup}')
    records = {name: (host.app / name).read_bytes() if (host.app / name).exists() else None for name in RECORDS}
    (backup / 'records.json').write_text(json.dumps({k: v.decode() if v is not None else None for k, v in records.items()}))
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    staged = backup / 'new-app'
    shutil.copytree(package / 'backend/app', staged)
    try:
        host.control('stop')
        os.rename(host.app / 'backend/app', backup / 'previous-app')
        os.rename(staged, host.app / 'backend/app')
        host.control('start')
        host.wait_health()
        # Keep old hashed assets for already-open browser tabs; index is replaced last.
        for source in (package / 'web').rglob('*'):
            if source.is_dir():
                target_dir = host.app / 'web-dist' / source.relative_to(package / 'web')
                target_dir.mkdir(parents=True, exist_ok=True)
                target_dir.chmod(0o755)
            if source.is_file() and source != package / 'web/index.html':
                target = host.app / 'web-dist' / source.relative_to(package / 'web')
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(target, source.read_bytes())
        atomic_write(host.app / 'web-dist/index.html', (package / 'web/index.html').read_bytes())
        verify_live(package, manifest, host)
        for name in RECORDS[:2]:
            atomic_write(host.app / name, (manifest['commit'] + '\n').encode())
        atomic_write(host.app / 'RELEASE.json', json.dumps({
            'commit': manifest['commit'], 'status': 'verified', 'backup': str(backup),
            'verified_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'files': manifest['files'],
        }, indent=2).encode())
    except BaseException as failure:
        try:
            host.control('stop')
            # Retain failed contents and backup, never delete a production tree.
            if (host.app / 'backend/app').exists():
                os.rename(host.app / 'backend/app', backup / 'failed-app')
            shutil.copytree(backup / 'backend-app', host.app / 'backend/app')
            os.rename(host.app / 'web-dist', backup / 'failed-web')
            shutil.copytree(backup / 'web', host.app / 'web-dist')
            for name, value in records.items():
                path = host.app / name
                if value is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, value)
            host.control('start')
            host.wait_health()
            if files(host.app / 'backend/app') != files(backup / 'backend-app') or files(host.app / 'web-dist') != files(backup / 'web'):
                raise RuntimeError('Rollback content mismatch')
            host.public(backup / 'web')
        except BaseException as rollback:
            raise RuntimeError(f'RELEASE FAILED AND ROLLBACK FAILED; backup={backup}; '
                               f'release={failure}; rollback={rollback}') from rollback
        raise RuntimeError(f'Release failed; rollback verified; backup={backup}; {failure}') from failure
    print(json.dumps({'status': 'verified', 'commit': manifest['commit'], 'backup': str(backup)}))


@contextlib.contextmanager
def lock(root):
    path = root / '.release.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def deploy(args):
    bundle = Path(args.bundle).resolve()
    if digest(bundle) != args.sha256:
        raise ValueError('Bundle SHA256 mismatch')
    host = Host()
    with lock(host.root), tempfile.TemporaryDirectory(prefix='crm-release-', dir=host.root) as tmp:
        package = Path(tmp)
        with tarfile.open(bundle) as tar:
            required = sum(member.size for member in tar.getmembers())
        if shutil.disk_usage(host.root).free < required * 3 + 128 * 1024 * 1024:
            raise ValueError('Insufficient disk space to stage package safely')
        unpack(bundle, package)
        manifest = verify_package(package)
        preflight(package, manifest, host)
        if args.apply:
            install(package, manifest, host)
        else:
            print(json.dumps({'status': 'preflight_only', 'commit': manifest['commit']}))


def main():
    def interrupted(signum, frame):
        raise RuntimeError(f'Release interrupted by signal {signum}')

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    pack = commands.add_parser('build')
    pack.add_argument('--ref', required=True)
    pack.add_argument('--baseline', required=True, help='Git commit of verified live backend')
    pack.add_argument('--output', required=True)
    pack.add_argument('--allow-downloads', action='store_true', help='Fetch missing lockfile-pinned dependencies')
    pack.set_defaults(func=build)
    release = commands.add_parser('deploy')
    release.add_argument('--bundle', required=True)
    release.add_argument('--sha256', required=True)
    release.add_argument('--apply', action='store_true', help='Actually change production and restart')
    release.set_defaults(func=deploy)
    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
