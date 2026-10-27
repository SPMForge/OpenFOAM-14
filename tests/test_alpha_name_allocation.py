"""Shared isolated Git/API contract for new Alpha name allocation."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('name_inputs', ROOT / 'scripts/release_inputs.py')
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)

class AlphaNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'wrapper'
        self.remote = Path(self.temp.name) / 'origin.git'
        self.git('init', '--bare', str(self.remote), cwd=Path(self.temp.name))
        self.git('init', str(self.root), cwd=Path(self.temp.name))
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        self.git('commit', '--allow-empty', '-m', 'Fixture')
        self.git('remote', 'add', 'origin', str(self.remote))

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.root, check=True, capture_output=True, text=True)

    def allocate(self, tag='1.2.3-alpha.1', **kwargs):
        return inputs.find_available_alpha(self.root, 'example/wrapper', tag, inputs.increment_alpha_tag, **kwargs)

    def release(self, tag, draft=False):
        return {'id': 1, 'tag_name': tag, 'draft': draft, 'assets': []}

    def test_absence_permits_first_candidate(self):
        with patch.object(inputs, 'github_json', return_value=None):
            result = self.allocate()
        self.assertEqual(result['tag'], '1.2.3-alpha.1')
        self.assertEqual(result['occupied_tags'], [])

    def test_local_and_remote_tags_both_reserve_names(self):
        self.git('tag', '1.2.3-alpha.1')
        self.git('tag', '1.2.3-alpha.2')
        self.git('push', 'origin', 'refs/tags/1.2.3-alpha.2')
        self.git('tag', '-d', '1.2.3-alpha.2')
        with patch.object(inputs, 'github_json', return_value=None):
            result = self.allocate()
        self.assertEqual(result['tag'], '1.2.3-alpha.3')
        self.assertEqual(result['occupied_tags'], ['1.2.3-alpha.1', '1.2.3-alpha.2'])

    def test_consecutive_orphan_and_draft_releases_reserve_names(self):
        with patch.object(inputs, 'github_json', side_effect=[self.release('1.2.3-alpha.1'), self.release('1.2.3-alpha.2', True), None]):
            self.assertEqual(self.allocate()['tag'], '1.2.3-alpha.3')

    def test_remote_failure_is_not_absence(self):
        self.git('remote', 'set-url', 'origin', str(self.remote / 'missing'))
        with patch.object(inputs, 'github_json', return_value=None), self.assertRaises(RuntimeError):
            self.allocate()

    def test_api_errors_abort_selection(self):
        for error in ['401', '403', '429', '503', 'EOF']:
            with self.subTest(error=error), patch.object(inputs, 'github_json', side_effect=RuntimeError(error)), self.assertRaises(RuntimeError):
                self.allocate()

    def test_malformed_release_is_not_absence(self):
        for payload in [{}, {'id': 1}, self.release('wrong'), {'id': 1, 'tag_name': '1.2.3-alpha.1', 'draft': False}]:
            with self.subTest(payload=payload), patch.object(inputs, 'github_json', return_value=payload), self.assertRaises(ValueError):
                self.allocate()

    def test_candidate_limit_is_enforced(self):
        with patch.object(inputs, 'github_json', side_effect=[self.release('1.2.3-alpha.1'), self.release('1.2.3-alpha.2')]), self.assertRaisesRegex(RuntimeError, 'limit'):
            self.allocate(max_candidates=2)

    def test_publish_recheck_rejects_release_created_after_resolve(self):
        with patch.object(inputs, 'github_json', return_value=None):
            result = self.allocate()
        with patch.object(inputs, 'github_json', return_value=self.release(result['tag'])), self.assertRaisesRegex(RuntimeError, 'occupied'):
            inputs.assert_alpha_available(self.root, 'example/wrapper', result['tag'])

    def test_publish_recheck_rejects_new_remote_tag(self):
        with patch.object(inputs, 'github_json', return_value=None):
            result = self.allocate()
        self.git('tag', result['tag'])
        self.git('push', 'origin', 'refs/tags/' + result['tag'])
        with patch.object(inputs, 'github_json', return_value=None), self.assertRaisesRegex(RuntimeError, 'occupied'):
            inputs.assert_alpha_available(self.root, 'example/wrapper', result['tag'])

    def test_increment_rejects_stable_and_invalid_names(self):
        for value in ['1.2.3', 'a\nb-alpha.1', '1.2.3-alpha.x']:
            with self.subTest(tag=value), self.assertRaises(ValueError):
                inputs.increment_alpha_tag(value)
