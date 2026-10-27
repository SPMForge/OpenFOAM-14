"""Verify candidate selection is connected to the shared helper before Build."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

class NameAdapterTests(unittest.TestCase):
    def test_workflow_guards_new_tags_and_reserves_before_build(self):
        workflows=list((ROOT/'.github/workflows').glob('*core.yml'))
        text='\n'.join(p.read_text() for p in workflows)
        resolve=text[text.index('  resolve:'):text.index('  build:')]
        if ROOT.name=='MoltenVK':
            self.assertIn('--reserve-alpha-names',resolve)
            self.assertIn('--reserved-next-alpha-version',text)
            self.assertIn('assert-alpha-available',(ROOT/'scripts/spm/publish_release.sh').read_text())
        else:
            self.assertIn('assert-alpha-available',text)
            self.assertIn('--github-repository' if ROOT.name in ('ncnn','OpenFOAM-14') else 'allocate-alpha',resolve)
        # Publication must not allocate another candidate after Build.
        later=text[text.index('  build:'):]
        self.assertNotIn(' allocate-alpha ',later)
        self.assertNotIn('--reserve-alpha-names',later)

    def test_source_selector_preserves_existing_and_propagates_new_candidate(self):
        if ROOT.name not in ('ncnn','OpenFOAM-14'):
            self.skipTest('This adapter uses a native planner, not the date-tag selector')
        from scripts.spm import select_upstream_tag as selector
        args=SimpleNamespace(repo_root=ROOT,release_channel='alpha',github_repository='example/wrapper')
        previous='1.0.20261020-alpha.1' if ROOT.name=='ncnn' else '14.0.20261008-alpha.1'
        upstream='20261020' if ROOT.name=='ncnn' else '20261008'
        fresh=previous.rsplit('.',1)[0]+'.7'
        with patch.object(selector,'_list_refs',return_value=['refs/tags/'+previous]), patch.object(selector.release_inputs,'find_available_alpha',return_value={'tag':fresh,'reason':'occupied names skipped'}):
            build,existing,next_tag=selector._resolve_release_tags(args,upstream)
        self.assertEqual((build,existing,next_tag),(previous,previous,fresh))
        args.release_channel='stable'
        with patch.object(selector.release_inputs,'find_available_alpha',side_effect=AssertionError('stable must not allocate')):
            self.assertEqual(selector._resolve_release_tags(args,upstream)[1:],('',''))
