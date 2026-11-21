"""Isolated filesystem/service simulation: no SSH, production DB or real restart."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import subprocess
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('release', Path(__file__).with_name('release.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def write(root, name, text):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


class FakeHost(r.Host):
    def __init__(self, root):
        super().__init__(root)
        self.events = []
        self.health_failures = 0
        self.public_failures = 0
        self.starts_fail = False

    def layout(self):
        self.events.append('layout')

    def control(self, action):
        self.events.append(action)
        if action == 'start' and self.starts_fail:
            raise RuntimeError('simulated start failure')

    def healthy(self):
        return True

    def wait_health(self):
        self.events.append('health')
        if self.health_failures:
            self.health_failures -= 1
            raise RuntimeError('simulated health failure')

    def public(self, web):
        self.events.append('public')
        if self.public_failures:
            self.public_failures -= 1
            raise ValueError('simulated public mismatch')


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='crm-release-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = FakeHost(self.root / 'host')
        self.package = self.root / 'package'
        self.package.mkdir()
        write(self.host.app, 'backend/app/main.py', 'version = "old"\n')
        write(self.host.app, 'backend/.env', 'PRIVATE=preserve\n')
        write(self.host.app, 'backend/uploads/original.txt', 'business file\n')
        write(self.host.app, 'backend/requirements.txt', 'fastapi==1\n')
        write(self.host.app, 'backend/alembic.ini', '[alembic]\n')
        write(self.host.app, 'backend/alembic/versions/a.py', 'revision = "a"\n')
        write(self.host.app, 'web-dist/index.html', '<html>old</html>')
        write(self.host.app, 'web-dist/assets/old-hash.js', 'old asset')
        write(self.host.app, 'DEPLOYED_COMMIT', 'a' * 40 + '\n')
        write(self.host.app, 'WEB_DEPLOYED_COMMIT', 'c' * 40 + '\n')
        write(self.package, 'backend/app/main.py', 'version = "new"\n')
        write(self.package, 'backend/requirements.txt', 'fastapi==1\n')
        write(self.package, 'backend/alembic.ini', '[alembic]\n')
        write(self.package, 'backend/alembic/versions/a.py', 'revision = "a"\n')
        write(self.package, 'web/index.html', '<script src="/assets/new-hash.js"></script>')
        write(self.package, 'web/assets/new-hash.js', 'new asset')
        self.manifest = {
            'schema': 1, 'commit': 'b' * 40, 'baseline_commit': 'a' * 40,
            'files': r.files(self.package),
            'baseline_app': r.files(self.host.app / 'backend/app'),
            'guards': r.subset(self.host.app, r.GUARDS),
        }
        self.save_manifest()

    def save_manifest(self):
        write(self.package, 'manifest.json', json.dumps(self.manifest))

    def apply(self):
        manifest = r.verify_package(self.package)
        r.preflight(self.package, manifest, self.host)
        r.install(self.package, manifest, self.host)

    def test_success_two_versions_match_and_keep_business_files_and_old_assets(self):
        self.apply()
        for name in r.RECORDS[:2]:
            self.assertEqual((self.host.app / name).read_text(), 'b' * 40 + '\n')
        record = json.loads((self.host.app / 'RELEASE.json').read_text())
        self.assertEqual(record['status'], 'verified')
        self.assertTrue(Path(record['backup']).is_dir())
        self.assertEqual((self.host.app / 'backend/.env').read_text(), 'PRIVATE=preserve\n')
        self.assertEqual((self.host.app / 'backend/uploads/original.txt').read_text(), 'business file\n')
        self.assertTrue((self.host.app / 'web-dist/assets/old-hash.js').is_file())
        self.assertEqual(self.host.events.count('start'), 1)

    def test_health_failure_restores_code_web_and_mixed_old_records(self):
        before = r.files(self.host.app)
        self.host.health_failures = 1
        with self.assertRaisesRegex(RuntimeError, 'rollback verified'):
            self.apply()
        self.assertEqual(r.files(self.host.app), before)
        self.assertEqual(self.host.events.count('start'), 2)

    def test_public_failure_after_web_switch_restores_both(self):
        before = r.files(self.host.app)
        self.host.public_failures = 1
        with self.assertRaisesRegex(RuntimeError, 'rollback verified'):
            self.apply()
        self.assertEqual(r.files(self.host.app), before)

    def test_failed_rollback_reports_failure_and_keeps_backup(self):
        self.host.starts_fail = True
        with self.assertRaisesRegex(RuntimeError, 'ROLLBACK FAILED'):
            self.apply()
        self.assertTrue(list((self.host.root / 'backup').glob('unified-*/backend-app/main.py')))
        self.assertFalse((self.host.app / 'RELEASE.json').exists())

    def test_tampered_or_missing_payload_refused_before_service_stop(self):
        for mode in ('tampered', 'missing'):
            with self.subTest(mode=mode):
                p = self.package / 'backend/app/main.py'
                if mode == 'tampered':
                    p.write_text('changed')
                else:
                    p.unlink()
                with self.assertRaisesRegex(ValueError, 'hash/set mismatch'):
                    self.apply()
                self.assertNotIn('stop', self.host.events)

    def test_unknown_hotfix_refused(self):
        write(self.host.app, 'backend/app/hotfix.py', 'important = True')
        with self.assertRaisesRegex(ValueError, 'differs from baseline'):
            self.apply()
        self.assertNotIn('stop', self.host.events)

    def test_requirements_or_migration_changes_refused(self):
        for name in ('backend/requirements.txt', 'backend/alembic/versions/a.py'):
            with self.subTest(name=name):
                original = (self.host.app / name).read_text()
                write(self.host.app, name, 'changed')
                with self.assertRaisesRegex(ValueError, 'dependency/migration/config differs'):
                    self.apply()
                write(self.host.app, name, original)
        self.assertNotIn('stop', self.host.events)

    def test_symlink_destination_refused(self):
        (self.host.app / 'web-dist/escape').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, 'Symlink refused'):
            self.apply()

    def test_symlinked_backup_refused(self):
        (self.host.root / 'backup').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, 'Symlinked backup'):
            self.apply()

    def test_stale_bytecode_link_is_not_silently_followed_during_backup(self):
        (self.host.app / 'backend/app/__pycache__').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, 'Symlink refused'):
            self.apply()

    def test_insufficient_space_refused(self):
        with patch.object(r.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 0})()):
            with self.assertRaisesRegex(ValueError, 'Insufficient disk'):
                self.apply()
        self.assertNotIn('stop', self.host.events)

    def test_concurrent_release_refused(self):
        with r.lock(self.host.root):
            with self.assertRaises(BlockingIOError):
                with r.lock(self.host.root):
                    self.fail('must not acquire second lock')

    def test_unexpected_or_secret_payload_refused_even_with_updated_hashes(self):
        for name in ('backend/app/.env', 'backend/._main.py', 'other/private.txt'):
            write(self.package, name, 'secret')
            self.manifest['files'] = {k: v for k, v in r.files(self.package).items() if k != 'manifest.json'}
            self.save_manifest()
            with self.assertRaises(ValueError):
                r.verify_package(self.package)
            (self.package / name).unlink()

    def test_archive_traversal_symlink_duplicates_refused(self):
        for kind in ('traversal', 'symlink', 'duplicate'):
            with self.subTest(kind=kind):
                archive = self.root / (kind + '.tar')
                with tarfile.open(archive, 'w') as tar:
                    item = tarfile.TarInfo('../escape' if kind == 'traversal' else 'file')
                    if kind == 'symlink':
                        item.type = tarfile.SYMTYPE
                        item.linkname = '/etc/passwd'
                    tar.addfile(item, io.BytesIO(b''))
                    if kind == 'duplicate':
                        tar.addfile(item, io.BytesIO(b''))
                dest = self.root / ('dest-' + kind)
                dest.mkdir()
                with self.assertRaises(ValueError):
                    r.unpack(archive, dest)
                self.assertEqual(list(dest.iterdir()), [])

    def test_public_spa_health_html_is_not_accepted_as_backend_health(self):
        with patch.object(r, 'fetch', return_value=b'<html>SPA</html>'):
            self.assertFalse(r.Host(self.host.root).healthy())

    def test_health_wait_retries_starting_service(self):
        host = r.Host(self.host.root)
        with patch.object(r, 'run', side_effect=[subprocess.CalledProcessError(3, 'systemctl'), 'active']), \
                patch.object(host, 'healthy', return_value=True), patch.object(r.time, 'sleep'):
            host.wait_health()

    def test_wrong_bundle_digest_refused(self):
        bundle = self.root / 'bad.tar.gz'
        bundle.write_bytes(b'not even an archive')
        args = type('Args', (), {'bundle': str(bundle), 'sha256': '0' * 64, 'apply': True})()
        with self.assertRaisesRegex(ValueError, 'Bundle SHA256 mismatch'):
            r.deploy(args)
        self.assertNotIn('stop', self.host.events)

    def test_public_html_and_entry_assets_both_checked(self):
        host = r.Host(self.host.root)
        web = self.package / 'web'
        with patch.object(r, 'fetch', side_effect=[(web / 'index.html').read_bytes(), b'wrong asset']):
            with self.assertRaisesRegex(ValueError, 'entry resource differs'):
                host.public(web)

    def test_missing_record_preserved_after_rollback(self):
        (self.host.app / 'WEB_DEPLOYED_COMMIT').unlink()
        before = r.files(self.host.app)
        self.host.public_failures = 1
        with self.assertRaisesRegex(RuntimeError, 'rollback verified'):
            self.apply()
        self.assertEqual(r.files(self.host.app), before)

    def test_partial_version_write_failure_restores_original_records(self):
        before = r.files(self.host.app)
        replace = r.os.replace
        failed = False

        def fail_once(src, dst):
            nonlocal failed
            if Path(dst).name == 'WEB_DEPLOYED_COMMIT' and not failed:
                failed = True
                raise OSError('simulated version record write failure')
            return replace(src, dst)

        with patch.object(r.os, 'replace', side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, 'rollback verified'):
                self.apply()
        self.assertEqual(r.files(self.host.app), before)

    def test_default_deploy_is_preflight_only(self):
        args = type('Args', (), {'bundle': str(self.root / 'bundle.tar.gz'), 'apply': False})()
        with tarfile.open(args.bundle, 'w:gz') as tar:
            for p in self.package.rglob('*'):
                tar.add(p, arcname=p.relative_to(self.package).as_posix(), recursive=False)
        args.sha256 = r.digest(Path(args.bundle))
        with patch.object(r, 'Host', return_value=self.host), patch.object(r, 'install') as install:
            r.deploy(args)
            install.assert_not_called()
        self.assertNotIn('stop', self.host.events)


if __name__ == '__main__':
    unittest.main()
