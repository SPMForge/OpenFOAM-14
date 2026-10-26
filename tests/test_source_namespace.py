from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.spm import source_acquisition


class SourceNamespaceTests(unittest.TestCase):
    def test_fetch_isolates_upstream_tags_and_uses_contract_remote(self):
        for stale_remote in (False, True):
            with self.subTest(stale_remote=stale_remote), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'source'
                wrapper = root / 'wrapper'
                for repo in (source, wrapper):
                    repo.mkdir()
                    self.git(repo, 'init')
                self.git(source, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-m', 'source')
                self.git(source, 'tag', 'v1.2.3')
                if stale_remote:
                    self.git(wrapper, 'remote', 'add', 'upstream', str(root / 'obsolete-source'))
                contract = root / 'source.json'
                contract.write_text(json.dumps({
                    'upstream_remote_name': 'upstream',
                    'upstream_repository_url': str(source),
                    'upstream_tag_ref_prefix': 'refs/upstream-tags',
                }))
                source_acquisition._fetch_tags(wrapper, contract)
                self.assertEqual(self.git(wrapper, 'tag', '--list'), '')
                self.assertEqual(self.git(wrapper, 'rev-parse', 'refs/upstream-tags/v1.2.3'), self.git(source, 'rev-parse', 'HEAD'))

    def test_export_aborts_before_destination_cleanup_when_tag_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.git(root,'init')
            self.git(root,'-c','user.name=Test','-c','user.email=test@example.com','commit','--allow-empty','-m','one')
            expected=self.git(root,'rev-parse','HEAD')
            self.git(root,'-c','user.name=Test','-c','user.email=test@example.com','commit','--allow-empty','-m','two')
            self.git(root,'update-ref','refs/upstream-tags/20260113',self.git(root,'rev-parse','HEAD'))
            contract=root/'source.json'
            contract.write_text(json.dumps({'upstream_remote_name':'upstream','upstream_repository_url':str(root),'upstream_tag_ref_prefix':'refs/upstream-tags'}))
            destination=root/'export'
            destination.mkdir()
            (destination/'sentinel').write_text('preserve')
            with patch.dict('os.environ',{'SPMFORGE_EXPECTED_UPSTREAM_COMMIT':expected}):
                with self.assertRaisesRegex(ValueError,'tag moved'):
                    source_acquisition._export_source(root,contract,'20260113',destination)
            self.assertEqual((destination/'sentinel').read_text(),'preserve')

    def git(self, repo, *arguments):
        return subprocess.run(['git', *arguments], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
