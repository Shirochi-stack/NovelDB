"""Offline boundary tests; no Git commits, network, keys, scraping or translation."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import bridge
import build
import data
import maintenance


class DataTests(unittest.TestCase):
    def fixture(self, root, complete=True):
        folder = root / 'docs/data'
        folder.mkdir(parents=True)
        (folder / 'kpage_titles_en.txt').write_text('1|||original|||English\n')
        (folder / 'unrelated.txt').write_text('keep private')
        return data.pack(root, 'kpage', 'fixture-commit', root / 'artifacts/data', complete)

    def test_scoped_artifact_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            _, contents = data.verify(root / 'artifacts/data', 'fixture-commit')
            self.assertEqual(set(contents), {'docs/data/kpage_titles_en.txt'})

    def test_tampered_archive_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            (root / 'artifacts/data/candidate.zip').write_bytes(b'tampered')
            with self.assertRaisesRegex(bridge.Failure, 'digest'):
                data.verify(root / 'artifacts/data', 'fixture-commit')

    def test_incomplete_or_wrong_revision_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, False)
            for commit in ('fixture-commit', 'different'):
                with self.subTest(commit=commit), self.assertRaises(bridge.Failure):
                    data.verify(root / 'artifacts/data', commit)

    def test_path_traversal_and_other_sources_rejected(self):
        for name in ('../config.json', 'docs/data/../../config.json', 'docs/data/npia_top.json.gz', 'metadata/state/sfc.json.gz'):
            self.assertFalse(data.allowed(name, 'kpage'))

    def test_publication_gate_precedes_network_and_git(self):
        api = Mock()
        with patch.object(data.subprocess, 'run', side_effect=AssertionError('ran Git')):
            with self.assertRaises(bridge.Failure):
                data.publish({}, api=api)
        api.request.assert_not_called()

    def test_moved_branch_rejected_before_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            api = Mock(project='/projects/1')
            api.request.return_value = {'commit': {'id': 'new-commit'}}
            env = {'CI_COMMIT_REF_PROTECTED': 'true', 'AUTOMATION_WRITE_APPROVED': 'true', 'CI_COMMIT_BRANCH': 'main', 'CI_COMMIT_SHA': 'fixture-commit'}
            with patch.object(data.subprocess, 'run', side_effect=AssertionError('staged data')):
                with self.assertRaisesRegex(bridge.Failure, 'Branch moved'):
                    data.publish(env, root, api)

    def test_incomplete_translation_is_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'rows.txt'
            path.write_text('1|||source|||\n')
            with self.assertRaisesRegex(bridge.Failure, 'remain untranslated'):
                build.verify_translation(path)
            path.write_text('1|||source|||Translated\n')
            build.verify_translation(path)

    def test_translation_approval_precedes_execution(self):
        runner = bridge.Runner('fixture', plan=True)
        runner.plan = False
        runner.env = {}
        with patch.object(runner, 'command', side_effect=AssertionError('called translation')):
            with self.assertRaises(bridge.Failure):
                build.translate(runner, 'rows.txt', 'korean', 'titles')

    def test_release_and_pages_writes_need_explicit_gate(self):
        with self.assertRaises(bridge.Failure):
            maintenance.releases({}, True, Mock())
        with self.assertRaises(bridge.Failure):
            maintenance.pages({})

    def test_browser_cache_outside_job_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work = root / 'job'
            cache = root / 'shared'
            work.mkdir(); cache.mkdir()
            runner = Mock(plan=False, work=work, env={'PLAYWRIGHT_BROWSERS_PATH': str(cache)})
            with self.assertRaisesRegex(bridge.Failure, 'escapes'):
                build.strip_browser(runner)
            runner.command.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
