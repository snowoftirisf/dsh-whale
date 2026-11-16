"""Download public model data only; no model repository code is executed."""
import os
from pathlib import Path
os.environ['HF_HUB_DISABLE_XET'] = '1'
for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
    os.environ.pop(key, None)
from huggingface_hub import snapshot_download

root = Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2]) / 'voice-data' / 'models'
models = [
    ('csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17','sensevoice-int8', ['model.int8.onnx','tokens.txt']),
    ('Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign','qwen-voicedesign',None),
    ('Qwen/Qwen3-TTS-12Hz-1.7B-Base','qwen-base',None),
]
if __name__ == '__main__':
    import sys
    for repo, folder, allow in models:
        if len(sys.argv)>1 and sys.argv[1] != folder:
            continue
        print('Downloading '+repo, flush=True)
        snapshot_download(repo, local_dir=root/folder, allow_patterns=allow,
                          ignore_patterns=['*.md','*.png','*.jpg','.gitattributes'], max_workers=3)
        print('Ready '+folder, flush=True)
