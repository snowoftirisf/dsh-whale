"""Rebind a restored data folder; never change current host/profile settings."""
import argparse
import json
from pathlib import Path
from data_paths import read_data_manifest, resolve_data_paths


def rebase_data(root, apply=False):
    paths = resolve_data_paths(root=root)
    read_data_manifest(root=root)
    config_file = paths['config'] / 'wxauto.local.json'
    config = json.loads(config_file.read_text(encoding='utf-8-sig'))
    previous = str(config['dataRoot'])
    config.update(dataRoot=str(paths['root']), imageDirectory=str(paths['media'] / 'images'),
                  attachmentDirectory=str(paths['media'] / 'attachments'),
                  statePath=str(paths['config'] / 'receipts.local.json'),
                  workspaceMigrationTarget=str(paths['config'] / 'workspace'))
    changes = [(config_file, json.dumps(config, ensure_ascii=False, indent=2) + '\n')]
    policy_file = paths['config'] / 'memory-policy.json'
    if policy_file.exists():
        policy = json.loads(policy_file.read_text(encoding='utf-8-sig'))
        policy['metadataFile'] = str(paths['memory'] / 'provenance.json')
        changes.append((policy_file, json.dumps(policy, ensure_ascii=False, indent=2) + '\n'))
    for file in (paths['persona'] / 'persona.md', paths['config'] / 'workspace/AGENTS.md'):
        if file.exists():
            text = file.read_text(encoding='utf-8-sig')
            for old in {previous, previous.replace('\\', '/')}:
                text = text.replace(old, str(paths['root']).replace('\\', '/'))
            changes.append((file, text))
    voice_file = paths['voice'] / 'voice.local.json'
    if voice_file.exists():
        voice = json.loads(voice_file.read_text(encoding='utf-8-sig'))
        if voice.get('referenceAudioBase') == 'dataRoot':
            reference = paths['root'] / voice['referenceAudio']
            if not reference.resolve().is_relative_to(paths['voice'].resolve()) or not reference.is_file():
                raise RuntimeError('restored-voice-reference-invalid')
    if apply:
        for file, text in changes:
            temporary = file.with_name(file.name + '.rebase.tmp')
            with temporary.open('x', encoding='utf8') as output:
                output.write(text)
                output.flush()
                import os
                os.fsync(output.fileno())
            temporary.replace(file)
    return {'dataRoot': str(paths['root']), 'previousRoot': previous, 'applied': apply,
            'files': len(changes), 'runtimeBindingsToVerify': ['workspace', 'python', 'codexBin', 'memeRoots', 'modelRoot'],
            'note': 'Host runtimes and WeChat login must be rebound on the destination; no host settings were changed.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True)
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    print(json.dumps(rebase_data(a.root, a.apply), ensure_ascii=False))
