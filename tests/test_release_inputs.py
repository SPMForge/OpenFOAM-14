import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_inputs', ROOT / 'scripts/release_inputs.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git('init')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        (self.root / 'config').mkdir()
        (self.root / 'config/source_acquisition.json').write_text(json.dumps({'upstream_repository_url':'https://example.invalid/upstream.git'}))
        (self.root / 'builder.py').write_text('build = 1\n')
        self.git('add', '.')
        self.git('commit', '-m', 'initial')
        self.commit = self.git('rev-parse','HEAD')
        self.git('update-ref','refs/upstream-tags/v1.2.3',self.commit)

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True, text=True, capture_output=True).stdout.strip()

    def tag_record(self):
        module.capture(self.root,'v1.2.3')
        self.git('add', module.RECORD)
        self.git('commit','-m','metadata')
        self.git('tag','1.2.3-alpha.1')

    def test_identity_distinguishes_absent_legacy_invalid_matching_and_changed(self):
        module.capture(self.root, 'v1.2.3')
        self.assertEqual(module.tag_input_state(self.root, 'missing'), 'tag_absent')
        self.git('tag', 'legacy')
        self.assertEqual(module.tag_input_state(self.root, 'legacy'), 'identity_missing')
        (self.root / module.RECORD).write_text('{invalid')
        self.git('add', module.RECORD)
        self.git('commit', '-m', 'invalid metadata')
        self.git('tag', 'invalid')
        module.capture(self.root, 'v1.2.3')
        self.assertEqual(module.tag_input_state(self.root, 'invalid'), 'identity_invalid')
        self.tag_record()
        self.assertEqual(module.tag_input_state(self.root, '1.2.3-alpha.1'), 'identity_matches')
        (self.root / 'builder.py').write_text('build = 2\n')
        module.capture(self.root, 'v1.2.3')
        self.assertEqual(module.tag_input_state(self.root, '1.2.3-alpha.1'), 'identity_differs')

    def test_same_inputs_match_despite_generated_metadata_commit(self):
        self.tag_record()
        module.capture(self.root,'v1.2.3')
        self.assertTrue(module.tag_inputs_match(self.root,'1.2.3-alpha.1'))

    def test_wrapper_source_change_requires_evaluation(self):
        self.tag_record()
        (self.root/'builder.py').write_text('build = 2\n')
        module.capture(self.root,'v1.2.3')
        self.assertFalse(module.tag_inputs_match(self.root,'1.2.3-alpha.1'))

    def test_moved_upstream_tag_requires_evaluation(self):
        self.tag_record()
        self.git('update-ref','refs/upstream-tags/v1.2.3',self.git('rev-parse','HEAD'))
        module.capture(self.root,'v1.2.3')
        self.assertFalse(module.tag_inputs_match(self.root,'1.2.3-alpha.1'))

    def test_source_export_rejects_tag_that_moved_after_resolution(self):
        self.tag_record()
        self.git('update-ref','refs/upstream-tags/v1.2.3',self.git('rev-parse','HEAD'))
        with self.assertRaisesRegex(ValueError,'tag moved'):
            module.resolve_source(self.root,'v1.2.3',self.commit)

    def test_dependency_pin_and_platform_changes_are_build_inputs(self):
        for name in ['platforms.json','moltenvk_dependency.json','UpstreamRepository.txt']:
            with self.subTest(name=name):
                path=self.root/'config'/name
                path.write_text('old')
                self.git('add', str(path.relative_to(self.root)))
                old=module.wrapper_digest(self.root)
                path.write_text('new')
                self.assertNotEqual(old,module.wrapper_digest(self.root))

    def test_legacy_tag_without_identity_is_not_proof_of_reuse(self):
        self.git('tag','1.2.3-alpha.1')
        module.capture(self.root,'v1.2.3')
        self.assertFalse(module.tag_inputs_match(self.root,'1.2.3-alpha.1'))

    def run_guard(self, channel='alpha'):
        output=self.root/'github-output.txt'
        output.write_text('mode=skip\n')
        process=subprocess.run([
            __import__('sys').executable,str(ROOT/'scripts/release_inputs.py'),
            'guard','--repo-root',str(self.root),'--tag','1.2.3-alpha.1',
            '--release-channel',channel,'--github-output',str(output),
        ],capture_output=True,text=True)
        return process,output.read_text()

    def test_complete_same_input_guard_preserves_skip(self):
        self.tag_record()
        process,output=self.run_guard()
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertEqual(output,'mode=skip\n')

    def test_changed_input_guard_overrides_skip(self):
        self.tag_record()
        (self.root/'builder.py').write_text('build = 2\n')
        module.capture(self.root,'v1.2.3')
        process,output=self.run_guard()
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertIn('mode=evaluate',output)

    def test_stable_input_drift_aborts_without_changing_decision(self):
        self.tag_record()
        (self.root/'builder.py').write_text('build = 2\n')
        module.capture(self.root,'v1.2.3')
        process,output=self.run_guard('stable')
        self.assertNotEqual(process.returncode,0)
        self.assertIn('refusing reuse',process.stderr)
        self.assertEqual(output,'mode=skip\n')

    def test_github_server_failure_is_not_missing_release(self):
        result=subprocess.CompletedProcess([],1,'','gh: Bad Gateway (HTTP 502)')
        with patch.object(module.subprocess,'run',return_value=result):
            with self.assertRaisesRegex(RuntimeError,'Unable to establish'):
                module.github_json('repos/owner/repo/releases/latest')

    def test_github_404_is_missing_release(self):
        result=subprocess.CompletedProcess([],1,'','gh: Not Found (HTTP 404)')
        with patch.object(module.subprocess,'run',return_value=result):
            self.assertIsNone(module.github_json('repos/owner/repo/releases/latest'))
