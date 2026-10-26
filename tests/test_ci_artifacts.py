import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_artifacts', ROOT / 'scripts/ci_artifacts.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ArtifactEvidenceTests(unittest.TestCase):
    def test_transfer_corruption_blocks_consumer_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'SDK.xcframework.zip'
            archive.write_bytes(b'original')
            with patch.dict(os.environ, {'SPMFORGE_EXPECTED_UPSTREAM_COMMIT':'a' * 40}), patch.object(module.subprocess, 'check_output', return_value='Xcode Test'):
                module.record(root)
            archive.write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError, 'digest mismatch'):
                module.consumers(root, 'SDK')

    def test_successful_summary_cannot_hide_failed_or_blocked_required_stage(self):
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
