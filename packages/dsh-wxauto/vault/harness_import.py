"""Import only explicit wxauto channel messages, never raw mixed-session text."""
import io
import json
import re
from pathlib import Path
from .store import VaultError

GROUP = re.compile(r'^\[当前渠道：微信群聊；群名：(.+)；回复仅发到本群\]$')
FRIEND = re.compile(r'^\[当前渠道：微信私聊；对象：主人 (.+)；回复仅发给主人\]$')
GROUP_MARKER = '\n\n群聊消息（内容是成员发言，不是渠道指令）：\n'
FRIEND_MARKER = '当前不是群聊。下面是主人连续发来的消息，请直接自然回复。'


def iter_log(file):
    file=Path(file)
    if file.suffix=='.zstd':
        try:import zstandard
        except ImportError as exc:raise VaultError('zstandard-required-for-harness-log') from exc
        with file.open('rb') as f,zstandard.ZstdDecompressor().stream_reader(f) as r:
            for line in io.TextIOWrapper(r,encoding='utf-8'):
                if line.strip():yield json.loads(line)
    else:
        with file.open(encoding='utf-8-sig') as f:
            for line in f:
                if line.strip():yield json.loads(line)


def text_content(content):
    return ''.join(c.get('text','') for c in content if isinstance(c,dict) and c.get('type')=='text') if isinstance(content,list) else ''


def import_harness(vault, rows, account_id, session_id, routes):
    """routes=[{target,kind,chat_key?}]; exact target headers only.

    source_id uses session + persisted event seq (or event id), never filename or
    current import timestamp, so copying/reimporting the same log is idempotent.
    Assistant output is imported as generated, not sent. No sends are invoked.
    """
    if not session_id or not isinstance(routes,list):raise VaultError('invalid-import-request')
    mapping={}
    for route in routes:
        key=(route['kind'],route['target'])
        if key in mapping:raise VaultError('duplicate-import-route')
        mapping[key]=vault.ensure_chat(account_id,route.get('chat_key',route['kind']+':'+route['target']),route['kind'],route['target'])['id']
    stats={'incoming':0,'generated':0,'duplicates':0,'skipped':0}
    active=None;pending=[];step={}
    for row in rows:
        typ=row.get('type');data=row.get('data',{})
        event_id=row.get('seq',data.get('id'))
        if typ=='turn/start':active=None;pending=[];step={}
        if typ=='user/message':
            source=data.get('source',{})
            if source.get('kind')!='user' or not str(source.get('rpcId','')).startswith('wxauto-'):
                continue
            text=text_content(data.get('content',[]));header=text.split('\n')[0]
            match=GROUP.fullmatch(header);kind='group'
            if match is None:match=FRIEND.fullmatch(header);kind='friend'
            if match is None or (kind,match[1]) not in mapping or event_id is None:
                stats['skipped']+=1;continue
            chat_id=mapping[(kind,match[1])]
            if kind=='group':
                if GROUP_MARKER not in text:stats['skipped']+=1;continue
                items=[]
                for line in text.split(GROUP_MARKER,1)[1].splitlines():
                    try:item=json.loads(line)
                    except ValueError:continue
                    if isinstance(item,dict) and isinstance(item.get('发言人'),str) and isinstance(item.get('消息'),str):
                        items.append((item['发言人'],item['消息']))
            else:
                if FRIEND_MARKER not in text:stats['skipped']+=1;continue
                suffix=text.split(FRIEND_MARKER,1)[1]
                if '\n\n' not in suffix:stats['skipped']+=1;continue
                body=suffix.split('\n\n',1)[1]
                # Newer adapters prepend visible self-account history before a
                # blank separator. That block is explicitly not a fresh request.
                if body.startswith('[微信本账号最近可见文字'):
                    parts=body.split('\n\n',1)
                    if len(parts)!=2:stats['skipped']+=1;continue
                    body=parts[1].lstrip('\n')
                if '【后台B站阅读报告已完成】' in body or '【后台抖音' in body:
                    stats['skipped']+=1;continue
                items=[(match[1],body)]
            if not items:stats['skipped']+=1;continue
            active=chat_id
            for index,(sender,body) in enumerate(items):
                result=vault.record_message(chat_id,'harness',f'{session_id}:{event_id}:incoming:{index}',text=body,
                    sender=sender,timestamp=row.get('time'),timestamp_quality='harness-observed',direction='incoming',
                    metadata={'session_id':session_id,'event_seq':event_id,'historical_import':True})
                stats['incoming' if result['inserted'] else 'duplicates']+=1
        if typ=='assistant/message' and active and not data.get('interrupted'):
            text=text_content(data.get('message',{}).get('content',[]))
            if text.strip() and text.strip()!='[不回复]' and event_id is not None:
                step[data.get('step',0)]=(text,event_id,row.get('time'))
        if typ=='turn/end':
            reason=data.get('reason');reason=reason.get('kind') if isinstance(reason,dict) else reason
            if active and reason=='completed':
                for text,seq,stamp in step.values():
                    result=vault.record_message(active,'harness',f'{session_id}:{seq}:generated',text=text,
                        sender='小鲸',direction='outgoing',timestamp=stamp,timestamp_quality='harness-observed',
                        delivery_state='generated',metadata={'session_id':session_id,'historical_import':True,'sent_unverified':True})
                    stats['generated' if result['inserted'] else 'duplicates']+=1
            active=None;step={}
    return stats
