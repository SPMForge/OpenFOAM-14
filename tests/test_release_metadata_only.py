from pathlib import Path
import importlib.util
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]


class MetadataOnlyRepairTests(unittest.TestCase):
    def plan(self,same_inputs,draft=False):
        if ROOT.name in {'ncnn','OpenFOAM-14'}:
            from scripts.spm import release_state as module
            upstream='20260113' if ROOT.name=='ncnn' else '20261008'
            version='1.0.20260113-alpha.1' if ROOT.name=='ncnn' else '14.0.20261008-alpha.1'
            with patch.object(module.release_inputs,'tag_inputs_match',return_value=same_inputs):
                return module.inspect_release_state(package_tag=version,upstream_tag=upstream,release_channel='alpha',tag_exists=True,
                    release_view={'isPrerelease':draft,'isDraft':draft,'assets':[{'name':name} for name in module.required_release_asset_names(upstream)]},latest_release_tag=None)['mode']
        script=ROOT/('scripts/spm_release.py' if ROOT.name=='libwebp' else 'scripts/spm/release_publication.py')
        spec=importlib.util.spec_from_file_location('metadata_repair_'+ROOT.name,script)
        module=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=module
        spec.loader.exec_module(module)
        if ROOT.name=='libwebp':
            state=SimpleNamespace(release_exists=True,release_is_prerelease=draft,release_is_draft=draft,release_is_latest=False,
                release_asset_names=module.required_release_asset_names('1.6.0-alpha.1'),release_id=1)
            with patch.object(module,'inspect_github_release_state',return_value=state),patch.object(module.release_inputs,'tag_inputs_match',return_value=same_inputs):
                return module.inspect_release(repository='SPMForge/libwebp',package_tag='1.6.0-alpha.1',upstream_tag='v1.6.0',release_channel='alpha',tag_exists=True)['mode']
        version='1.2.3-alpha.1'
        release=module.GitHubRelease(tag_name=version,assets=module.required_release_assets(version),is_prerelease=draft,is_draft=draft)
        with patch.object(module,'fetch_github_release',return_value=release),patch.object(module,'fetch_latest_release_tag',return_value=None),patch.object(module.release_inputs,'tag_inputs_match',return_value=same_inputs):
            return module.inspect_target_state('SPMForge/MoltenVK','v1.2.3',version,'alpha',{version}).publication_mode

    def test_same_inputs_and_complete_assets_repair_only_metadata(self):
        self.assertEqual(self.plan(True),'metadata')

    def test_changed_inputs_do_not_take_metadata_shortcut(self):
        self.assertNotEqual(self.plan(False),'metadata')

    def test_draft_with_correct_channel_is_not_treated_as_complete(self):
        self.assertEqual(self.plan(True,draft=True),'metadata')
