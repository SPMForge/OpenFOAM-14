import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/upload_release_assets.py'
spec = importlib.util.spec_from_file_location('release_asset_upload', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseAssetUploadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.first = self.root / 'binary.zip'
        self.second = self.root / 'source.tar.gz'
        self.first.write_bytes(b'archive')
        self.second.write_bytes(b'source')

    def response(self, assets):
        return subprocess.CompletedProcess([], 0, json.dumps({'assets': assets}))

    def test_complete_identical_assets_do_not_upload(self):
        with patch.object(module.subprocess, 'run', return_value=self.response([
            {'name': self.first.name, 'digest': module.sha256(self.first)},
        ])) as run:
            self.assertEqual(module.upload_missing_assets('owner/repo', '1.0.0', [self.first]), [])
            self.assertEqual(run.call_count, 1)

    def test_only_missing_assets_are_uploaded_without_overwrite(self):
        with patch.object(module.subprocess, 'run', return_value=self.response([
            {'name': self.first.name, 'digest': module.sha256(self.first)},
        ])) as run:
            self.assertEqual(module.upload_missing_assets('owner/repo', '1.0.0', [self.first, self.second]), [self.second.name])
            args = run.call_args.args[0]
            self.assertIn(str(self.second), args)
            self.assertNotIn(str(self.first), args)
            self.assertNotIn('--clobber', args)

    def test_source_drift_aborts_before_uploading_any_missing_binary(self):
        with patch.object(module.subprocess, 'run', return_value=self.response([
            {'name': self.second.name, 'digest': 'sha256:' + '0' * 64},
        ])) as run:
            with self.assertRaisesRegex(ValueError, 'different published bytes'):
                module.upload_missing_assets('owner/repo', '1.0.0', [self.first, self.second])
            self.assertEqual(run.call_count, 1)

    def test_legacy_asset_is_downloaded_and_hashed(self):
        def execute(command, **kwargs):
            if 'stdout' in kwargs:
                kwargs['stdout'].write(self.first.read_bytes())
                return subprocess.CompletedProcess(command, 0)
            return self.response([{'name': self.first.name, 'id': 7, 'digest': None}])
        with patch.object(module.subprocess, 'run', side_effect=execute) as run:
            self.assertEqual(module.upload_missing_assets('owner/repo', '1.0.0', [self.first]), [])
            self.assertEqual(run.call_count, 2)

    def test_duplicate_names_fail_before_network_access(self):
        with patch.object(module.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'unique basenames'):
                module.upload_missing_assets('owner/repo', '1.0.0', [self.first, self.first])
            run.assert_not_called()
