"""Generate two auditions in the user's chosen style; never sends to WeChat."""
import contextlib
import json
from pathlib import Path
import sys
from voice_runtime import Speech,DATA

text='回来啦？快跟我说说，今天又遇到什么好玩的事了？别光顾着忙，我也想听听你的声音呀。'
descriptions=[
    ('A','年轻成年女性的声音，清亮自然，活泼俏皮，有细微笑意和自然情绪起伏。音高自然中高但不尖锐，中文口语流畅，亲近有灵气，语速轻快但不抢，像和熟人聊天，不要播音腔，不要夸张卖萌。'),
    ('B','年轻成年女性的声音，柔和明亮，俏皮中带一点慵懒和亲近。中文口语自然，有轻微笑意，句尾情绪灵动，语速舒展，音高适中，不尖不嗲，像朋友之间自在聊天，不要朗诵或播音腔。'),
]
if __name__=='__main__':
    speech=Speech()
    result=[]
    for name,description in descriptions:
        print('Generating audition '+name,file=sys.stderr,flush=True)
        with contextlib.redirect_stdout(sys.stderr):
            value=speech.run({'op':'design','text':text,'description':description})
            transcription=speech.run({'op':'transcribe','file':value['file']})
        result.append({'name':name,'description':description,'referenceText':text,
                       **value,'transcription':transcription})
        index=DATA/'auditions'/'index.local.json'
        index.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps({'name':name,**value},ensure_ascii=False),flush=True)
