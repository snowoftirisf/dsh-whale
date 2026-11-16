"""Actual private Node->Python paging over synthetic authenticated snapshots."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from vault.test_snapshot_import import make_snapshot,TALKER,TABLE,OWNER
from vault.store import digest_file


class PagingIoTests(unittest.TestCase):
    def test_real_private_client_collects_1001_inputs_before_returning_legacy_catalog(self):
        plugin=Path(__file__).parent.resolve()
        with tempfile.TemporaryDirectory(dir=plugin) as folder:
            root=Path(folder);snapshots=root/'snapshots/windows-live/snapshots';snapshots.mkdir(parents=True)
            initial=make_snapshot(snapshots/'initial',rows=[(i,500000+i,34,1772323200+i,2,'synthetic incoming voice',0) for i in range(1,1002)],hash_manifest=True)
            manifest_file=initial/'snapshot-manifest.json';manifest=json.loads(manifest_file.read_text())
            manifest['source_id']='fixture-source';manifest['audio_capture']={'enabled':True,'scope':'chat','schemaVersion':1,'databases':['message/media_0.db']}
            manifest['databases'].append({'database':'message/media_0.db','authenticated':True,'plaintext_sha256':'a'*64});manifest_file.write_text(json.dumps(manifest))
            snapshot_id=manifest['snapshot_id'];initial.rename(snapshots/snapshot_id)
            (root/'snapshots/windows-live/latest.json').write_text(json.dumps({'snapshot_id':snapshot_id,'import':{'account_id':'fixture-account'}}))
            (root/'config').mkdir();(root/'config/wxauto.local.json').write_text(json.dumps({'dataRoot':str(root),'accountId':'fixture-account','capture':{'accountUsername':OWNER,'sourceId':'fixture-source'}}))
            (root/'config/group-routes.local.json').write_text(json.dumps({'accountId':'fixture-account','groups':[{'nativeChatKey':'wechat:'+TALKER}]}))
            code="import {nativeVoiceClient} from "+json.dumps((plugin/'native-voice-client.mjs').as_uri())+";const c=nativeVoiceClient("+json.dumps({'root':str(plugin),'dataRoot':str(root),'python':sys.executable,'decoderPython':sys.executable})+");try{const v=await c.catalog(["+json.dumps('wechat:'+TALKER)+"]);console.log(JSON.stringify({source:v.source,count:v.voices.length,ids:new Set(v.voices.map(x=>x.nativeId)).size,snapshotId:v.snapshotId}));}finally{c.close();}"
            result=subprocess.run(['node','--input-type=module','-e',code],capture_output=True,encoding='utf8',timeout=30)
            self.assertEqual(result.returncode,0,result.stderr);value=json.loads(result.stdout)
            self.assertEqual(value,{'source':'authenticated-native-voice-catalog','count':1001,'ids':1001,'snapshotId':snapshot_id})

if __name__=='__main__':unittest.main()
