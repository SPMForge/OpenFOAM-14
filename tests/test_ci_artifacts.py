import importlib.util
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_artifacts', ROOT / 'scripts/ci_artifacts.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ArtifactEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / 'Package.swift').write_text('// isolated summary fixture\n')
        patcher = patch.object(module, 'ROOT', root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_transfer_corruption_blocks_consumer_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'SDK.xcframework.zip'
            archive.write_bytes(b'original')
            with patch.dict(os.environ, {'SPMFORGE_EXPECTED_UPSTREAM_COMMIT':'a' * 40, 'GITHUB_OUTPUT':'', 'GITHUB_STEP_SUMMARY':''}), patch.object(module.subprocess, 'check_output', return_value='Xcode Test'):
                module.record(root)
            archive.write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError, 'digest mismatch'):
                module.consumers(root, 'SDK')

    @patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'offline'))
    def test_successful_summary_cannot_hide_failed_or_blocked_required_stage(self, _api):
        with tempfile.TemporaryDirectory() as temporary:
            summary = Path(temporary) / 'summary.md'
            for result in ['failure', 'cancelled', 'skipped']:
                with self.subTest(result=result), patch.dict(os.environ, {
                    'CI_NEEDS': json.dumps({'verify':{'result':result},'resolve':{'result':'skipped'}}),
                    'GITHUB_STEP_SUMMARY':str(summary), 'GITHUB_REPOSITORY':'owner/repo', 'GITHUB_RUN_ID':'1',
                }):
                    with self.assertRaises(SystemExit):
                        module.summary()
                    self.assertIn(result, summary.read_text())

    @patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'offline'))
    def test_unexecuted_consumers_are_not_success_when_a_build_is_required(self, _api):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
            'CI_NEEDS': json.dumps({'verify':{'result':'success'},
                                   'resolve':{'result':'success','outputs':{'release_mode':'create'}},
                                   'build':{'result':'success'}, 'consumers':{'result':'skipped'}}),
            'GITHUB_STEP_SUMMARY':str(Path(temporary) / 'summary.md'),
            'GITHUB_REPOSITORY':'owner/repo', 'GITHUB_RUN_ID':'1',
        }):
            with self.assertRaises(SystemExit):
                module.summary()

    def test_inventory_excludes_untransferred_compile_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'SDK.xcframework.zip').write_bytes(b'archive')
            (root / 'staging').mkdir()
            (root / 'staging/object.o').write_bytes(b'compile output')
            with patch.dict(os.environ, {'GITHUB_OUTPUT':'', 'GITHUB_STEP_SUMMARY':''}), patch.object(module.subprocess, 'check_output', return_value='Xcode Test'):
                module.record(root)
            inventory = json.loads((root / 'ci-artifacts.json').read_text())
            self.assertEqual(set(inventory['files']), {'SDK.xcframework.zip'})
