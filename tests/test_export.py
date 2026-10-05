"""Verify complete project snapshots and safe failure behavior."""
from pathlib import Path
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import zipfile

EXPORTER = Path(__file__).resolve().parents[1] / 'export.py'

class ExportBootstrapQA(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='register-bootstrap-qa-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.project = self.base / 'project with spaces'
        self.project.mkdir()
        shutil.copy2(EXPORTER, self.project / 'export.py')

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.project), *args], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout

    def init_git(self):
        self.git('init', '--initial-branch=main')
        self.git('config', 'user.name', 'Snapshot QA')
        self.git('config', 'user.email', 'snapshot@example.invalid')
        (self.project / '.gitignore').write_text('ignored.txt\n')
        (self.project / 'register').write_text('original implementation\n')
        self.git('add', '.')
        self.git('commit', '-m', 'initial snapshot')

    def export(self, success=True, *args):
        result = subprocess.run(['python3', str(self.project / 'export.py'), *args], cwd=self.base,
                                capture_output=True, timeout=20)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stderr, b'')
            self.assertEqual(result.stdout.decode().strip(), str(self.project / 'register-snapshot.zip'))
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b'Traceback', result.stderr)
        return result

    def contents(self):
        with zipfile.ZipFile(self.project / 'register-snapshot.zip') as archive:
            return {item.filename: archive.read(item) for item in archive.infolist()}

    def test_no_git_keeps_original_docs_and_bytes(self):
        (self.project / 'register').write_bytes(b'CLI bytes\x00\xff')
        (self.project / 'README.odt').write_bytes(b'ODT binary\x00\xff')
        (self.project / 'register.bak_old').write_text('old useful source')
        self.export()
        contents = self.contents()
        self.assertEqual(contents['register/register'], b'CLI bytes\x00\xff')
        self.assertEqual(contents['register/README.odt'], b'ODT binary\x00\xff')
        self.assertEqual(contents['register/register.bak_old'], b'old useful source')
        self.assertFalse((self.project / '.git').exists())

    def test_git_captures_worktree_and_respects_ignores(self):
        self.init_git()
        head = self.git('rev-parse', 'HEAD')
        (self.project / 'register').write_text('local changes\n')
        (self.project / 'new.py').write_text('untracked source')
        (self.project / 'ignored.txt').write_text('ignored')
        before_index = (self.project / '.git' / 'index').read_bytes()
        self.export()
        self.assertEqual((self.project / '.git' / 'index').read_bytes(), before_index)
        contents = self.contents()
        self.assertEqual(contents['register/register'], b'local changes\n')
        self.assertEqual(contents['register/new.py'], b'untracked source')
        self.assertNotIn('register/ignored.txt', contents)
        self.assertEqual(head, self.git('rev-parse', 'HEAD'))
        self.assertFalse(any(name.startswith('register/.git/') for name in contents))
        reports = [payload for name, payload in contents.items() if name.endswith('SNAPSHOT.txt') or name.endswith('SNAPSHOT.json')]
        self.assertEqual(len(reports), 1)
        self.assertIn(head.strip(), reports[0])
        self.assertIn(b'local', contents['register/register'])

    def test_modes_and_runtime_exclusions(self):
        script = self.project / 'register'
        script.write_text('echo hello')
        script.chmod(0o751)
        bad_names = ['.register/register.sqlite3', '__pycache__/bytecode.pyc', '.venv/bin/python', '.env', '.ssh/id_rsa', 'secret.sqlite', 'file.pyc']
        for name in bad_names:
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('private data')
        opaque_db = self.project / 'unusual-runtime'
        opaque_db.write_bytes(b'SQLite format 3\x00' + b'actual private buffer content')
        self.export()
        with zipfile.ZipFile(self.project / 'register-snapshot.zip') as archive:
            for name in bad_names:
                self.assertNotIn('register/' + name, archive.namelist())
            self.assertNotIn('register/unusual-runtime', archive.namelist())
            self.assertTrue(archive.getinfo('register/register').external_attr >> 16 & 0o111)

    def test_source_archive_and_internal_symlink_preserved(self):
        source = self.project / 'assets' / 'source-data.zip'
        source.parent.mkdir()
        source.write_bytes(b'legitimate fixture archive')
        (self.project / 'source-link').symlink_to('assets/source-data.zip')
        self.export()
        with zipfile.ZipFile(self.project / 'register-snapshot.zip') as archive:
            self.assertEqual(archive.read('register/assets/source-data.zip'), b'legitimate fixture archive')
            entry = archive.getinfo('register/source-link')
            self.assertTrue(stat.S_ISLNK(entry.external_attr >> 16))
            self.assertEqual(archive.read(entry), b'assets/source-data.zip')

    def test_previous_output_is_not_reexported(self):
        (self.project / 'register').write_text('first source')
        self.export()
        (self.project / 'register').write_text('second source')
        self.export()
        contents = self.contents()
        self.assertEqual(contents['register/register'], b'second source')
        self.assertNotIn('register/register-snapshot.zip', contents)
        self.assertEqual([path.name for path in self.project.glob('*.zip')], ['register-snapshot.zip'])

    def test_source_metadata_collision_preserves_previous_output(self):
        (self.project / 'register').write_text('source to preserve')
        self.export()
        previous = (self.project / 'register-snapshot.zip').read_bytes()
        (self.project / 'SNAPSHOT.txt').write_text('user source document')
        self.export(False)
        self.assertEqual((self.project / 'register-snapshot.zip').read_bytes(), previous)
        self.assertEqual((self.project / 'SNAPSHOT.txt').read_text(), 'user source document')

    def test_external_link_failure_preserves_previous_output(self):
        (self.project / 'register').write_text('source to preserve')
        self.export()
        previous = (self.project / 'register-snapshot.zip').read_bytes()
        external = self.base / 'outside-secret'
        external.write_text('secret')
        (self.project / 'external').symlink_to(external)
        result = self.export(False)
        self.assertIn(b'symlink', result.stderr.lower())
        self.assertEqual((self.project / 'register-snapshot.zip').read_bytes(), previous)

    def test_concurrent_edit_preserves_previous_output(self):
        self.init_git()
        target = self.project / 'register'
        target.write_text('already dirty content')
        self.export()
        previous = (self.project / 'register-snapshot.zip').read_bytes()
        tools = self.base / 'mock-bin'
        tools.mkdir()
        wrapper = tools / 'git'
        wrapper.write_text("""#!/usr/bin/env python3
import os
from pathlib import Path
import sys
if 'ls-files' in sys.argv:
    counter = Path(os.environ['EXPORT_QA_COUNTER'])
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    if count == 2:
        Path(os.environ['EXPORT_QA_SOURCE']).write_text('changed concurrently')
os.execv(os.environ['EXPORT_QA_GIT'], [os.environ['EXPORT_QA_GIT'], *sys.argv[1:]])
""")
        wrapper.chmod(0o755)
        env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH'],
               'EXPORT_QA_COUNTER': str(self.base / 'counter'),
               'EXPORT_QA_SOURCE': str(target), 'EXPORT_QA_GIT': shutil.which('git')}
        result = subprocess.run(['python3', str(self.project / 'export.py')], cwd=self.base,
                                env=env, capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'changed during export', result.stderr)
        self.assertEqual((self.project / 'register-snapshot.zip').read_bytes(), previous)
        self.assertFalse(list(self.project.glob('.register-export-*')))

    def test_nested_project_does_not_use_parent_git(self):
        subprocess.run(['git', '-C', str(self.base), 'init', '--initial-branch=main'], check=True, capture_output=True)
        (self.base / 'outside-private.txt').write_text('not the requested project')
        (self.project / 'register').write_text('nested project source')
        self.export()
        contents = self.contents()
        self.assertEqual(contents['register/register'], b'nested project source')
        self.assertFalse(any('outside-private' in name for name in contents))
        self.assertFalse((self.project / '.git').exists())

if __name__ == '__main__':
    unittest.main(verbosity=2)
