"""Exercise the exact workflow publishing shell against local Git repositories."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/daily-monitor.yml'
REPORT = 'strategies/quant/monitor/reports/2026-11-05.md'
NAMES = 'strategies/quant/monitor/names.json'


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.STDOUT)


def write(root, path, content):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote, self.work, self.other = (self.root / x for x in ('remote.git', 'runner', 'other'))
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, capture_output=True)
        subprocess.run(['git', 'clone', str(self.remote), str(self.work)], check=True, capture_output=True)
        git(self.work, 'checkout', '-b', 'main')
        for key, value in [('user.name', 'Test'), ('user.email', 'test@example.invalid')]:
            git(self.work, 'config', key, value)
        write(self.work, 'data/X.csv', 'old market data\n')
        write(self.work, NAMES, '{}\n')
        write(self.work, 'README.md', 'initial\n')
        git(self.work, 'add', '.')
        git(self.work, 'commit', '-m', 'initial')
        git(self.work, 'push', '-u', 'origin', 'main')
        subprocess.run(['git', 'clone', '-b', 'main', str(self.remote), str(self.other)], check=True, capture_output=True)
        git(self.other, 'config', 'user.name', 'Other')
        git(self.other, 'config', 'user.email', 'other@example.invalid')
        block = WORKFLOW.read_text().split('      - name: Commit report\n', 1)[1].split('      - name:', 1)[0]
        self.script = '\n'.join(line[10:] for line in block.split('        run: |\n', 1)[1].splitlines())
        self.script = self.script.replace('${{ steps.day.outputs.date }}', '2026-11-05')

    def prepare(self):
        write(self.work, REPORT, 'new report\n')
        write(self.work, NAMES, '{"X": "Example"}\n')
        write(self.work, 'data/X.csv', 'fresh local market data\n')
        write(self.work, 'data/NEW.csv', 'new untracked data\n')

    def advance(self, path='README.md', value='remote change\n'):
        write(self.other, path, value)
        git(self.other, 'add', '.')
        git(self.other, 'commit', '-m', 'concurrent update')
        git(self.other, 'push', 'origin', 'main')

    def publish(self):
        env = dict(os.environ, TMPDIR=str(self.root))
        return subprocess.run(['bash', '-e', '-c', self.script], cwd=self.work,
                              env=env, text=True, capture_output=True)

    def assert_preserved(self):
        self.assertEqual((self.work / 'data/X.csv').read_text(), 'fresh local market data\n')
        self.assertEqual((self.work / 'data/NEW.csv').read_text(), 'new untracked data\n')
        self.assertEqual(git(self.work, 'worktree', 'list', '--porcelain').count('worktree '), 1)

    def test_dirty_downloads_publish_without_committing_data(self):
        self.prepare()
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.remote, 'show', 'main:' + REPORT), 'new report\n')
        self.assertEqual(git(self.remote, 'show', 'main:data/X.csv'), 'old market data\n')
        self.assertNotIn('data/NEW.csv', git(self.remote, 'ls-tree', '-r', '--name-only', 'main'))
        self.assert_preserved()

    def test_rebase_preserves_remote_and_local_data_changes(self):
        self.prepare()
        self.advance('data/X.csv', 'remote market data\n')
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.remote, 'show', 'main:' + REPORT), 'new report\n')
        self.assertEqual(git(self.remote, 'show', 'main:data/X.csv'), 'remote market data\n')
        self.assert_preserved()

    def test_report_conflict_fails_without_overwriting_remote(self):
        self.prepare()
        self.advance(REPORT, 'different already published report\n')
        before = git(self.remote, 'rev-parse', 'main')
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(git(self.remote, 'rev-parse', 'main'), before)
        self.assertEqual(git(self.remote, 'show', 'main:' + REPORT), 'different already published report\n')
        self.assert_preserved()

    def test_push_rejection_fails_and_cleans_worktree(self):
        self.prepare()
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        before = git(self.remote, 'rev-parse', 'main')
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(git(self.remote, 'rev-parse', 'main'), before)
        self.assert_preserved()

    def test_shallow_checkout_rebases_concurrent_remote_update(self):
        self.work = self.root / 'shallow-runner'
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', 'main',
                        self.remote.as_uri(), str(self.work)], check=True, capture_output=True)
        self.assertEqual(git(self.work, 'rev-parse', '--is-shallow-repository').strip(), 'true')
        self.prepare()
        self.advance()
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.remote, 'show', 'main:' + REPORT), 'new report\n')
        self.assertEqual(git(self.remote, 'show', 'main:README.md'), 'remote change\n')
        self.assert_preserved()
