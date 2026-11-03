import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

source = Path(__file__).parents[1] / 'release.py'
if not source.exists():
    source = Path('/usr/local/lib/learned-bot/release.py')
spec = importlib.util.spec_from_file_location('release', source)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'v9-001'
        self.bundle.mkdir()
        self.token = self.root / 'token'
        self.token.write_text('x' * 32)
        self.manifest = dict(release='v9-001', image='ghcr.io/apptive-game-team/ac-model-bot@sha256:' + 'a' * 64,
                             game_image='ghcr.io/apptive-game-team/ac-game-rust@sha256:' + 'b' * 64)
        for name, data in [('registry', {}), ('game-config', dict(models=[dict(version='v9')], assignments={'-1': 'v9'})), ('smoke', [])]:
            raw = json.dumps(data).encode()
            (self.bundle / (name + '.json')).write_bytes(raw)
            self.manifest[name + '_sha256'] = hashlib.sha256(raw).hexdigest()
        self.plan = release.plan(self.manifest, self.root, self.token)

    def test_fixed_manifest_and_hashes(self):
        self.assertEqual(self.plan['assignments'], {'-1': 'v9'})
        self.assertEqual(self.plan['container'], 'dev-model-v9-001')
        (self.bundle / 'registry.json').write_text('{} changed')
        with self.assertRaises(ValueError):
            release.plan(self.manifest, self.root, self.token)

    def test_tag_traversal_and_other_repository_rejected(self):
        for key, value in [('image', 'ghcr.io/apptive-game-team/ac-model-bot:latest'),
                           ('game_image', 'ghcr.io/other/game@sha256:' + 'a' * 64),
                           ('release', '../v9-001')]:
            with self.assertRaises(ValueError):
                release.plan(dict(self.manifest, **{key: value}), self.root, self.token)

    def test_patch_preserves_slot_identity_and_replaces_model_mounts(self):
        payload = dict(Labels={'wordonline.slot': 'green'}, Env=['DOMAIN=green', 'LEARNED_BOT_URL=old', 'BOT_AUTO_MATCH_ENABLED=true'],
                       HostConfig=dict(Binds=['/keys:/keys:ro', '/old:/learned-bot:ro'],
                                       Mounts=[dict(Target='/run/secrets/bot-token')]),
                       NetworkingConfig=dict(EndpointsConfig={'net': {'Aliases': ['green']}, 'dev-model-old': {}}))
        result = release.patch(copy.deepcopy(payload), self.plan)
        self.assertEqual(result['Labels']['wordonline.slot'], 'green')
        self.assertIn('DOMAIN=green', result['Env'])
        self.assertIn('BOT_AUTO_MATCH_ENABLED=false', result['Env'])
        self.assertNotIn('BOT_AUTO_MATCH_ENABLED=true', result['Env'])
        self.assertEqual(result['HostConfig']['Binds'], ['/keys:/keys:ro'])
        self.assertEqual(len(result['HostConfig']['Mounts']), 2)
        self.assertEqual(set(result['NetworkingConfig']['EndpointsConfig']), {'net', self.plan['network']})
        self.assertEqual(release.patch(copy.deepcopy(result), self.plan), result)

    def test_existing_release_cannot_be_overwritten(self):
        with patch.object(release, 'run', side_effect=['', 'existing', json.dumps([{'Config': {'Labels': {'wordonline.model-release': 'different'}}}])]):
            with self.assertRaisesRegex(ValueError, 'different contents'):
                release.prepare(self.plan)

    def test_probe_failure_does_not_delete_previous_containers(self):
        calls = []
        def docker(*args):
            calls.append(args)
            if args[1:3] == ('network', 'ls') or args[1] == 'ps':
                return ''
            return 'created'
        with patch.object(release, 'run', side_effect=docker), patch.object(release, 'probe', side_effect=subprocess.CalledProcessError(1, 'probe')), patch.object(release.time, 'sleep'):
            with self.assertRaises(subprocess.CalledProcessError):
                release.prepare(self.plan)
        self.assertFalse(any('rm' in call or 'stop' in call for call in calls))
        launch = next(call for call in calls if call[1] == 'run')
        self.assertIn('--read-only', launch)
        self.assertNotIn('-p', launch)

    def test_retained_candidates_are_bounded(self):
        with patch.object(release, 'run', side_effect=['', '', 'one\ntwo\nthree']):
            with self.assertRaisesRegex(ValueError, 'retire'):
                release.prepare(self.plan)

    def test_probe_rechecks_manifest_files(self):
        (self.bundle / 'smoke.json').write_text('[1]')
        with patch.object(release, 'run') as docker:
            with self.assertRaises(ValueError):
                release.probe(self.plan)
        docker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
