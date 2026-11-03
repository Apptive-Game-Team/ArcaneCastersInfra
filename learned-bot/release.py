"""dev 추론 후보를 검증하고 game 슬롯에 적용할 고정 배포 계획을 만듭니다."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def checked(path, digest):
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('invalid digest')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('release file hash differs')
    return json.loads(raw)


def plan(manifest, root, token):
    release = manifest['release']
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,39}', release):
        raise ValueError('invalid release name')
    for key, repository in [('image', 'ac-model-bot'), ('game_image', 'ac-game-rust')]:
        if not re.fullmatch('ghcr.io/apptive-game-team/' + repository + '@sha256:[0-9a-f]{64}', manifest[key]):
            raise ValueError('pinned approved repository image required')
    root = root.resolve(strict=True)
    bundle = (root / release).resolve(strict=True)
    if bundle.parent != root or not token.is_absolute() or not token.is_file():
        raise ValueError('bundle or token path invalid')
    if any(char in str(bundle) + str(token) for char in ',\n\r'):
        raise ValueError('invalid mount path')
    documents = {name: checked(bundle / (name + '.json'), manifest[name + '_sha256'])
                 for name in ('registry', 'game-config', 'smoke')}
    config = documents['game-config']
    assignments = config['assignments']
    if not assignments or len(assignments) > 256 or any(
            not re.fullmatch('-[1-9][0-9]*', key) for key in assignments):
        raise ValueError('negative bot assignments required')
    versions = {row['version'] for row in config['models']}
    if not set(assignments.values()) <= versions:
        raise ValueError('assignment version missing')
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return dict(release=release, identity=identity, image=manifest['image'],
                game_image=manifest['game_image'], bundle=str(bundle), token=str(token),
                container='dev-model-' + release, network='dev-model-' + release,
                assignments=assignments,
                hashes={name: manifest[name + '_sha256'] for name in ('registry', 'game-config', 'smoke')})


def probe(p):
    for name, digest in p['hashes'].items():
        checked(Path(p['bundle']) / (name + '.json'), digest)
    run('docker', 'exec', p['container'], 'python', '-m', 'arcane_bot.deployment_probe',
        '--url', 'http://' + p['container'] + ':18743',
        '--token-file', '/run/secrets/bot-token', '--config', '/models/game-config.json',
        '--requests', '/models/smoke.json')


def prepare(p):
    run('docker', 'pull', p['image'])
    existing = run('docker', 'ps', '-a', '--filter', 'name=^/' + p['container'] + '$', '--format', '{{.Names}}')
    if existing:
        info = json.loads(run('docker', 'inspect', p['container']))[0]
        if info['Config']['Labels'].get('wordonline.model-release') != p['identity']:
            raise ValueError('release name already has different contents')
        run('docker', 'start', p['container'])
    else:
        retained = run('docker', 'ps', '-a', '--filter', 'label=wordonline.model-release', '--format', '{{.Names}}')
        if len(retained.splitlines()) >= 3:
            raise ValueError('three retained candidates; retire an unused release before deploying')
        networks = run('docker', 'network', 'ls', '--filter', 'name=^' + p['network'] + '$', '--format', '{{.Name}}')
        if networks:
            network = json.loads(run('docker', 'network', 'inspect', p['network']))[0]
            if not network['Internal'] or network['Labels'].get('wordonline.model-release') != p['identity']:
                raise ValueError('network ownership differs')
        else:
            run('docker', 'network', 'create', '--internal', '--label',
                'wordonline.model-release=' + p['identity'], p['network'])
        run('docker', 'run', '-d', '--name', p['container'], '--hostname', p['container'],
            '--network', p['network'], '--restart', 'unless-stopped', '--read-only',
            '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--pids-limit', '32', '--cpus', '0.5', '--memory', '768m', '--memory-swap', '768m',
            '--tmpfs', '/tmp:size=32m,mode=1777',
            '--label', 'wordonline.model-release=' + p['identity'],
            '--label', 'com.centurylinklabs.watchtower.enable=false',
            '--mount', 'type=bind,src=' + p['bundle'] + ',dst=/models,readonly',
            '--mount', 'type=bind,src=' + p['token'] + ',dst=/run/secrets/bot-token,readonly',
            p['image'], '--registry', '/models/registry.json', '--token-file', '/run/secrets/bot-token',
            '--host', p['container'], '--port', '18743')
    for attempt in range(30):
        try:
            probe(p)
            return
        except subprocess.CalledProcessError:
            if attempt == 29:
                raise
            time.sleep(2)


def patch(payload, p):
    payload['Labels']['wordonline.learned-release'] = p['identity']
    payload['Env'] = [v for v in payload['Env'] if not v.startswith(('LEARNED_BOT_', 'BOT_AUTO_MATCH_ENABLED='))]
    payload['Env'] += ['LEARNED_BOT_CONFIG=/learned-bot/game-config.json',
                       'LEARNED_BOT_URL=http://' + p['container'] + ':18743/infer',
                       'LEARNED_BOT_TOKEN_FILE=/run/secrets/bot-token', 'BOT_AUTO_MATCH_ENABLED=false']
    host = payload['HostConfig']
    destinations = {'/learned-bot', '/run/secrets/bot-token'}
    host['Binds'] = [b for b in host.get('Binds') or [] if b.split(':')[1] not in destinations]
    host['Mounts'] = [m for m in host.get('Mounts') or [] if m['Target'] not in destinations]
    host['Mounts'] += [dict(Type='bind', Source=p['bundle'], Target='/learned-bot', ReadOnly=True),
                       dict(Type='bind', Source=p['token'], Target='/run/secrets/bot-token', ReadOnly=True)]
    networks = payload['NetworkingConfig']['EndpointsConfig']
    payload['NetworkingConfig']['EndpointsConfig'] = {
        key: value for key, value in networks.items() if not key.startswith('dev-model-')}
    payload['NetworkingConfig']['EndpointsConfig'][p['network']] = {}
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'patch', 'probe'])
    parser.add_argument('file', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--payload', type=Path)
    args = parser.parse_args()
    data = json.loads(args.file.read_text())
    if args.command == 'prepare':
        data = plan(data, Path(os.environ['LEARNED_BOT_BUNDLE_ROOT']), Path(os.environ['LEARNED_BOT_TOKEN_FILE']))
        prepare(data)
        args.output.write_text(json.dumps(data))
    elif args.command == 'probe':
        probe(data)
    else:
        args.payload.write_text(json.dumps(patch(json.loads(args.payload.read_text()), data)))


if __name__ == '__main__':
    main()
