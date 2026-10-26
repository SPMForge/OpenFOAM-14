"""Protect the Actions graph's failure boundaries and publication gates."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CIStageTests(unittest.TestCase):
    def test_staged_graph_blocks_publication_on_consumer_failure(self):
        files = list((ROOT / '.github/workflows').glob('*core.yml')) + [ROOT / '.github/workflows/validate-apple-release-pipeline.yml']
        for path in files:
            with self.subTest(workflow=path.name):
                parsed = subprocess.run(['ruby', '-rjson', '-ryaml', '-e',
                                         'puts JSON.generate(YAML.load_file(ARGV[0]))', str(path)],
                                        check=True, capture_output=True, text=True)
                jobs = json.loads(parsed.stdout)['jobs']
                self.assertTrue({'verify', 'resolve', 'build', 'consumers', 'summary'} <= jobs.keys())
                self.assertEqual(jobs['summary']['if'], 'always()')
                self.assertEqual(jobs['summary']['name'], 'Summary')
                self.assertEqual(jobs['consumers']['needs'], ['resolve', 'build'])
                self.assertTrue(any('ci_artifacts.py consumers' in step.get('run', '') for step in jobs['consumers']['steps']))
                if path.name.endswith('core.yml'):
                    publish = jobs.get('publish', jobs.get('release'))
                    self.assertIn('consumers', publish['needs'])
                    self.assertIn("needs.consumers.result == 'success'", publish['if'])
                self.assertEqual(jobs['build']['permissions'], {'contents': 'read'})
