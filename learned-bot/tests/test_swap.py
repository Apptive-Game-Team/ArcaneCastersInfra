"""deployer 이미지에서 Docker와 HTTP 경계를 대체하고 실제 swap.sh를 실행합니다."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


FAKE = r'''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['FAKE_STATE'])
s = json.loads(p.read_text())
a = sys.argv[1:]
tool = Path(sys.argv[0]).name
s['calls'].append([tool] + a)
def done(value='', code=0):
    p.write_text(json.dumps(s))
    if value: print(value)
    sys.exit(code)
if tool == 'docker':
    if a[0] in ('pull', 'exec'): done()
    if a[:2] == ['image','inspect']:
        done('new-image' if '-f' in a else '[{"Config":{}}]')
    if a[0] == 'ps':
        names = list(s['slots']) if '-a' in a else [n for n,v in s['slots'].items() if v['running']]
        done('\n'.join(names))
    name = a[-1]
    if a[0] == 'inspect':
        v = s['slots'][name]
        if '-f' in a:
            fmt = a[a.index('-f')+1]
            done(v['image'] if fmt == '{{.Image}}' else v['release'] if 'learned-release' in fmt else name)
        done(json.dumps([dict(Config=dict(Env=['DOMAIN='+name], Labels={'wordonline.slot':name}), HostConfig={}, NetworkSettings=dict(Networks={'net':{}}))]))
    if a[0] == 'rm': done()
    if a[0] == 'start': s['slots'][name]['running']=True; done()
    if a[0] == 'stop': s['slots'][name]['running']=False; done()
    if a[0] == 'logs': done()
if tool == 'curl':
    url = next(x for x in a if x.startswith('http://'))
    if '--config' in a: sys.stdin.read()
    if '/containers/create?' in url:
        name=url.split('name=')[1]
        payload=json.loads(Path(a[a.index('--data')+1][1:]).read_text())
        s['slots'][name].update(image=payload['Image'], release=payload['Labels']['wordonline.learned-release'])
        done('{}')
    if '/actuator/health' in url: done('{"status":"UP"}')
    if '/learned-bots' in url: done('{}' if s['mode']=='unready' else '{"-1":"v9"}')
    if '/draining' in url: done(code=1 if s['mode']=='drain-fail' else 0)
if tool == 'timeout':
    if s['mode']=='drain-timeout': done(code=124)
    s['slots'][a[-1]]['running']=False
    done()
if tool == 'sleep': done()
done('unexpected fake command', 2)
'''


@unittest.skipUnless(Path('/usr/local/lib/learned-bot/release.py').exists(), 'run in the deployer image')
class SwapTests(unittest.TestCase):
    def scenario(self, mode, both=False, matches=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'fake'
            fake.write_text(FAKE)
            fake.chmod(0o755)
            for command in ('docker', 'curl', 'timeout', 'sleep'):
                (root / command).symlink_to(fake)
            state = dict(mode=mode, calls=[], slots={
                'blue': dict(running=True, image='old-image', release='old'),
                'green': dict(running=both, image='new-image' if both else 'old-image', release='candidate' if both and matches else 'old')})
            state_path = root / 'state.json'
            state_path.write_text(json.dumps(state))
            token = root / 'token'
            token.write_text('test.payload.signature')
            hashes = {}
            for name in ('registry', 'game-config', 'smoke'):
                (root / (name + '.json')).write_text('{}')
                hashes[name] = hashlib.sha256(b'{}').hexdigest()
            plan = dict(identity='candidate', game_image='pinned-image', container='dev-model-test',
                        bundle=str(root), hashes=hashes, token=str(token), network='dev-model-test', assignments={'-1':'v9'})
            plan_path = root / 'plan.json'
            plan_path.write_text(json.dumps(plan))
            env = dict(os.environ, PATH=str(root)+':'+os.environ['PATH'], FAKE_STATE=str(state_path),
                       SWAP_LOCK_HELD='1', IMAGE='ignored', CICD_TOKEN='test-token',
                       GROUP_LABEL='wordonline.group=dev-game', LEARNED_BOT_PLAN=str(plan_path),
                       LEARNED_BOT_SERVER_TOKEN_FILE=str(token), SETTLE_DELAY='0', DRAIN_TIMEOUT='1',
                       SPOOL_DIR=str(root / 'spool'))
            result = subprocess.run(['bash', '/usr/local/bin/swap.sh'], env=env, capture_output=True, text=True, timeout=15)
            return result, json.loads(state_path.read_text())

    def test_success_recreates_idle_then_drains_old(self):
        result, state = self.scenario('ok')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(state['slots']['blue']['running'])
        self.assertTrue(state['slots']['green']['running'])
        self.assertEqual(state['slots']['green']['release'], 'candidate')

    def test_unready_candidate_preserves_live_slot(self):
        result, state = self.scenario('unready')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(state['slots']['blue']['running'])
        self.assertFalse(state['slots']['green']['running'])
        self.assertFalse(any('/draining' in str(call) for call in state['calls']))

    def test_drain_failure_and_timeout_never_force_stop(self):
        for mode in ('drain-fail', 'drain-timeout'):
            result, state = self.scenario(mode)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(all(slot['running'] for slot in state['slots'].values()))
            self.assertFalse(any(call[:2] == ['docker', 'stop'] for call in state['calls']))

    def test_interrupted_swap_checks_release_before_retiring(self):
        result, state = self.scenario('ok', both=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(state['slots']['blue']['running'])
        result, state = self.scenario('ok', both=True, matches=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(all(slot['running'] for slot in state['slots'].values()))
