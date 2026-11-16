import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch
from message_watch import MessageCursor, SessionGate, MainWindowMonitor, event_key

class Row:
    ClassName = 'mmui::ChatTextItemView'
    def __init__(self, key, text): self.key, self.Name = key, text
    def GetRuntimeId(self): return (self.key,)
    def GetChildren(self): return []


class ReadFixtures(unittest.TestCase):
    """Message routing fixtures inject rendering decisions; no live UI imports."""
    def setUp(self):
        for fixture in (patch.object(MainWindowMonitor,'prepare_direction_read',lambda self:None),
                patch.object(MainWindowMonitor,'message_direction',lambda self,row:getattr(row,'direction','friend')),
                patch.object(MainWindowMonitor,'parse_private',lambda self,row,name:self.parse_incoming(row))):
            fixture.start();self.addCleanup(fixture.stop)


class StartupUnreadTests(ReadFixtures):
    def monitor(self, rows, infos, *, original='owner'):
        state = {'current': original, 'rows': rows, 'infos': infos, 'selected': []}
        wheel = SimpleNamespace(WheelDown=lambda **kwargs: None,
                                GetScrollPattern=lambda: SimpleNamespace(VerticallyScrollable=False))
        wx = SimpleNamespace(_api=SimpleNamespace(_chat_api=SimpleNamespace(msgbox=wheel)))
        monitor = MainWindowMonitor(wx, {name: 'friend' if name == 'owner' else 'group' for name in rows},
                                    receive_voice=True, image_directory='synthetic-images', recover_startup_unread=True)
        monitor.current = lambda: state['current']
        monitor.sessions = lambda: {name: dict(info) for name, info in state['infos'].items()}
        def select(name):
            state['selected'].append(name); state['current'] = name
            state['infos'][name] = {**state['infos'][name], 'isnew': False, 'new_count': 0}
        monitor.select = select
        monitor.controls = lambda: state['rows'][state['current']]
        return monitor, state

    @staticmethod
    def info(text, count=0):
        return {'time': '12:00', 'content': text, 'isnew': count > 0, 'new_count': count}

    @staticmethod
    def parse(row, api):
        return SimpleNamespace(attr=getattr(row, 'attr', 'friend'), type=getattr(row, 'kind', 'text'),
                               content=row.Name, sender='member', to_text=lambda: 'actual voice transcript')

    def modules(self):
        return patch.dict(sys.modules, {'wxauto4.msgs.msg': SimpleNamespace(parse_msg=self.parse),
            'incoming_images': SimpleNamespace(download_image=lambda *a, **k: {'path': 'synthetic-image.wav'})})

    def test_startup_unread_text_survives_selection_clearing_badge_and_only_tail_emits(self):
        old, new = Row(1, 'already read'), Row(2, 'new during maintenance')
        monitor, state = self.monitor({'owner': [old, new]}, {'owner': self.info(new.Name, 1)})
        with self.modules():
            monitor.initialize()
            self.assertEqual(state['selected'], [])
            self.assertNotIn('owner', monitor.cursor.rows)
            self.assertEqual([r['text'] for r in monitor.read('owner')], [new.Name])
            self.assertEqual(monitor.read('owner'), [])
        self.assertEqual(monitor.startup_pending, {})

    def test_startup_voice_and_image_use_existing_media_paths_not_history_replay(self):
        for kind, klass, label, preview in [('voice', 'mmui::ChatVoiceItemView', '语音3"秒未播放', '[语音] 3"'),
                ('image', 'mmui::ChatImageItemView', '图片', '[图片]')]:
            with self.subTest(kind=kind):
                row = Row(2, label); row.ClassName = klass; row.kind = kind
                monitor, state = self.monitor({'owner': [Row(1, 'old'), row]}, {'owner': self.info(preview, 1)})
                with self.modules():
                    monitor.initialize(); result = monitor.read('owner')
                    self.assertEqual(len(result), 1); self.assertEqual(result[0]['mediaType'], kind)
                    self.assertEqual(monitor.read('owner'), [])
                self.assertNotIn('owner', monitor.startup_pending)

    def test_read_history_is_seeded_without_parsing_and_idle_does_not_switch(self):
        monitor, state = self.monitor({'owner': [Row(1, 'old private')], 'group': [Row(2, 'old group')]},
            {'owner': self.info('old private'), 'group': self.info('old group')})
        with patch.dict(sys.modules, {'wxauto4.msgs.msg': SimpleNamespace(parse_msg=lambda *a: self.fail('old history parsed'))}):
            monitor.initialize(); state['selected'].clear()
            for _ in range(4):
                self.assertEqual(monitor.candidates(), ['owner'])
                self.assertEqual(monitor.read('owner'), [])
        self.assertNotIn('group', state['selected'])

    def test_badge_arriving_between_initial_snapshot_and_selection_is_preserved(self):
        monitor, state = self.monitor({'owner': [Row(1, 'new')]}, {'owner': self.info('old')})
        original = monitor.sessions; calls = [0]
        def sessions():
            calls[0] += 1
            if calls[0] == 2: state['infos']['owner'] = self.info('new', 1)
            return original()
        monitor.sessions = sessions
        with self.modules():
            monitor.initialize(); self.assertEqual(state['selected'], [])
            self.assertEqual([r['text'] for r in monitor.read('owner')], ['new'])

    def test_arrival_during_baseline_without_count_is_uncertain_not_replayed_or_acknowledged(self):
        monitor, state = self.monitor({'owner': [Row(1, 'old')], 'group': [Row(2, 'new race')]},
            {'owner': self.info('old'), 'group': self.info('old preview')})
        select = monitor.select
        def racing_select(name):
            select(name)
            if name == 'group': state['infos']['group'] = self.info('new race')
        monitor.select = racing_select
        monitor.initialize(); state['selected'].clear()
        self.assertIn('group', monitor.startup_uncertain)
        self.assertNotIn('group', monitor.gate.handled)
        self.assertNotIn('group', monitor.candidates()); self.assertEqual(state['selected'], [])
        with self.assertRaisesRegex(RuntimeError, 'boundary-unproven'): monitor.read('group')
        self.assertEqual(state['selected'], [])

    def test_unproven_tail_large_count_and_self_suffix_never_ack_or_emit(self):
        for case in ('mismatch', 'too-many', 'self', 'system'):
            with self.subTest(case=case):
                row = Row(1, 'tail'); rows = [row]; info = self.info('tail', 1)
                if case == 'mismatch': info = self.info('not visible', 1)
                if case == 'too-many': info = self.info('tail', 21)
                if case == 'self': row.attr = 'self'
                if case == 'system':
                    sep = Row(2, 'unknown system notice'); sep.ClassName = 'mmui::ChatItemView'
                    rows = [Row(3, 'first'), sep, row]; info = self.info('tail', 2)
                monitor, state = self.monitor({'owner': rows}, {'owner': info})
                with self.modules():
                    monitor.initialize()
                    with self.assertRaises(RuntimeError): monitor.read('owner')
                self.assertIn('owner', monitor.startup_pending)
                self.assertNotIn('owner', monitor.gate.handled)

    def test_time_separator_does_not_count_as_unread_message(self):
        first, last = Row(1, 'first unread'), Row(3, 'second unread')
        sep = Row(2, '12:00'); sep.ClassName = 'mmui::ChatItemView'
        monitor, state = self.monitor({'owner': [Row(0, 'old'), first, sep, last]},
                                      {'owner': self.info(last.Name, 2)})
        with self.modules():
            monitor.initialize()
            self.assertEqual([r['text'] for r in monitor.read('owner')], [first.Name, last.Name])

    def test_startup_self_suffix_is_rejected_before_any_avatar_parser(self):
        first,last=Row(1,'incoming'),Row(2,'our own last message')
        last.direction='self'
        monitor,_=self.monitor({'group':[first,last]}, {'group':self.info(last.Name,2)},original='group')
        with patch.object(monitor,'parse_incoming',side_effect=AssertionError('avatar parser called')):
            with self.assertRaisesRegex(RuntimeError,'sender-or-type-unproven'):
                monitor.startup_tail('group',[first,last],self.info(last.Name,2))

    def test_startup_unknown_direction_never_falls_back_to_avatar_parser(self):
        row=Row(1,'unknown row')
        monitor,_=self.monitor({'group':[row]}, {'group':self.info(row.Name,1)},original='group')
        with patch.object(monitor,'message_direction',side_effect=RuntimeError('message-direction-unknown')), \
                patch.object(monitor,'parse_incoming',side_effect=AssertionError('avatar parser called')):
            with self.assertRaisesRegex(RuntimeError,'message-direction-unknown'):
                monitor.startup_tail('group',[row],self.info(row.Name,1))

    def test_startup_private_uses_bound_peer_parser_after_direction_check(self):
        row=Row(1,'private incoming')
        monitor,_=self.monitor({'owner':[row]}, {'owner':self.info(row.Name,1)})
        message=SimpleNamespace(attr='friend',type='text')
        with patch.object(monitor,'parse_private',return_value=message) as typed, \
                patch.object(monitor,'parse_incoming',side_effect=AssertionError('avatar parser called')):
            self.assertEqual(monitor.startup_tail('owner',[row],self.info(row.Name,1)),[row])
            typed.assert_called_once_with(row,'owner')

    def test_startup_new_badge_count_before_first_read_includes_arrival_after_initialize(self):
        monitor, state = self.monitor({'owner': [Row(1, 'first')]}, {'owner': self.info('first', 1)})
        monitor.initialize()
        state['rows']['owner'].append(Row(2, 'second'))
        state['infos']['owner'] = self.info('second', 2)
        with self.modules():
            self.assertEqual([r['text'] for r in monitor.read('owner')], ['first', 'second'])

    def test_same_text_in_old_viewport_without_bottom_proof_never_replays(self):
        for pattern in (None, SimpleNamespace(VerticallyScrollable=True, VerticalScrollPercent=30)):
            monitor, state = self.monitor({'owner': [Row(1, 'same text')]}, {'owner': self.info('same text', 1)})
            monitor.wx._api._chat_api.msgbox.GetScrollPattern = lambda: pattern
            monitor.initialize()
            with self.modules(), self.assertRaisesRegex(RuntimeError, 'bottom-unverified'):
                monitor.read('owner')
            self.assertIn('owner', monitor.startup_pending)
            self.assertNotIn('owner', monitor.gate.handled)


    def test_uncertain_startup_does_not_disable_later_proven_current_chat_input(self):
        monitor, state = self.monitor({'owner': [Row(1, 'startup ambiguous')]}, {'owner': self.info('old preview')})
        select = monitor.select
        def racing_select(name):
            select(name); state['infos']['owner'] = self.info('startup ambiguous')
        monitor.select = racing_select
        monitor.initialize()
        self.assertIn('owner', monitor.startup_uncertain)
        self.assertEqual(monitor.candidates(), [])
        monitor.select = select
        state['rows']['owner'].append(Row(2, 'definitely arrived later'))
        state['infos']['owner'] = self.info('definitely arrived later')
        self.assertEqual(monitor.candidates(), ['owner'])
        with self.modules():
            self.assertEqual([r['text'] for r in monitor.read('owner')], ['definitely arrived later'])
        self.assertEqual(monitor.candidates(), [])


    def test_failed_startup_proof_does_not_idle_switch_back_to_cleared_badge(self):
        monitor, state = self.monitor({'owner': [Row(1, 'old')], 'group': [Row(2, 'unread')]},
            {'owner': self.info('old'), 'group': self.info('unread', 1)})
        monitor.wx._api._chat_api.msgbox.GetScrollPattern = lambda: None
        monitor.initialize()
        with self.modules(), self.assertRaisesRegex(RuntimeError, 'bottom-unverified'):
            monitor.read('group')
        monitor.select('owner'); state['selected'].clear()
        for _ in range(3): self.assertNotIn('group', monitor.candidates())
        self.assertEqual(state['selected'], [])
        state['infos']['group'] = self.info('fresh later', 1)
        self.assertIn('group', monitor.candidates())


    def test_default_false_retains_original_seed_ack_and_has_no_experimental_hold(self):
        monitor, state = self.monitor({'owner': [Row(1, 'maintenance unread')]},
                                      {'owner': self.info('maintenance unread', 1)})
        monitor.recover_startup_unread = False
        monitor.wx._api._chat_api.msgbox.GetScrollPattern = lambda: None
        monitor.initialize()
        self.assertIn('owner', monitor.cursor.rows)
        self.assertIn('owner', monitor.gate.handled)
        self.assertEqual(monitor.startup_pending, {})
        self.assertEqual(monitor.startup_uncertain, set())
        self.assertEqual(monitor.startup_last_attempt, {})
        with self.modules(): self.assertEqual(monitor.read('owner'), [])
        # Later fresh input continues despite a real missing ScrollPattern.
        state['rows']['owner'].append(Row(2, 'fresh after initialization'))
        state['infos']['owner'] = self.info('fresh after initialization')
        with self.modules():
            self.assertEqual([r['text'] for r in monitor.read('owner')], ['fresh after initialization'])
        self.assertEqual(monitor.candidates(), ['owner'])

    def test_constructor_defaults_to_disabled_and_candidate_holds_do_not_affect_default_path(self):
        monitor = MainWindowMonitor(SimpleNamespace(), {'owner': 'friend'})
        self.assertFalse(monitor.recover_startup_unread)
        monitor.current = lambda: 'owner'
        monitor.sessions = lambda: {'owner': self.info('old')}
        monitor.startup_uncertain.add('owner')
        monitor.startup_pending['non-route'] = self.info('held', 1)
        self.assertEqual(monitor.candidates(), ['owner'])


class CursorTests(ReadFixtures):
    def voice_monitor(self, rows, preview, baseline=None):
        wheel=SimpleNamespace(WheelDown=lambda **kwargs:None)
        monitor=MainWindowMonitor(SimpleNamespace(_api=SimpleNamespace(_chat_api=SimpleNamespace(msgbox=wheel))),
                                  {'owner':'friend'}, receive_voice=True)
        monitor.cursor.seed('owner',baseline if baseline is not None else [Row(1,'old text')])
        monitor.select=lambda name:None
        monitor.current=lambda:'owner'
        monitor.controls=lambda:rows
        monitor.sessions=lambda:{'owner':preview}
        return monitor,wheel

    def test_refreshed_self_row_is_committed_without_entering_sdk_sender_parser(self):
        old,new=Row(1,'old'),Row(2,'our own refreshed bubble');new.direction='self'
        monitor,_=self.voice_monitor([old,new],{'time':'11:40','content':new.Name,'isnew':False,'new_count':0},[old])
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *a:self.fail('own avatar parser called'))}):
            self.assertEqual(monitor.read('owner'),[])
            self.assertEqual(monitor.read('owner'),[])
        self.assertIn((2,),[key for key,_ in monitor.cursor.rows['owner']])

    def test_unreadable_direction_does_not_fall_back_to_avatar_parser_or_consume_input(self):
        old,new=Row(1,'old'),Row(2,'fresh incoming')
        monitor,_=self.voice_monitor([old,new],{'time':'11:40','content':new.Name,'isnew':False,'new_count':0},[old])
        parsed=[]
        def parse(c,p):
            parsed.append(c.key)
            return SimpleNamespace(attr='friend',type='text',sender='owner',content=c.Name)
        with patch.object(monitor,'message_direction',side_effect=[RuntimeError('message-direction-unknown'),'friend']), \
                patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=parse)}):
            self.assertEqual(monitor.read('owner'),[])
            self.assertEqual(parsed,[])
            self.assertEqual([r['text'] for r in monitor.read('owner')],['fresh incoming'])
            self.assertEqual(parsed,[2])

    def test_clipped_pending_before_valid_friend_does_not_block_and_later_recovers_once(self):
        old,clipped,fresh=Row(1,'old'),Row(2,'temporarily clipped'),Row(3,'fresh valid')
        monitor,_=self.voice_monitor([old,clipped,fresh],{'time':'11:40','content':fresh.Name,'isnew':False,'new_count':0},[old])
        parsed=[];readable=False
        def direction(c):
            if c is clipped and not readable:raise RuntimeError('message-direction-row-outside-view')
            return 'friend'
        def parse(c,p):
            parsed.append(c.key)
            return SimpleNamespace(attr='friend',type='text',sender='full group nickname',content=c.Name)
        with patch.object(monitor,'message_direction',side_effect=direction), \
                patch.object(monitor,'prepare_direction_read') as prepare, \
                patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=parse)}):
            first=monitor.read('owner')
            self.assertEqual([r['text'] for r in first],[fresh.Name])
            self.assertEqual(first[0]['sender'],'full group nickname')
            self.assertEqual(parsed,[3])
            self.assertNotIn((2,),[key for key,_ in monitor.cursor.rows['owner']])
            self.assertNotIn(__import__('message_watch').signature(clipped),monitor.cursor.accepted['owner'])
            self.assertEqual(monitor.read('owner'),[])
            self.assertEqual(parsed,[3]);prepare.assert_not_called()
            readable=True
            self.assertEqual([r['text'] for r in monitor.read('owner')],[clipped.Name])
            self.assertEqual(monitor.read('owner'),[])
            self.assertEqual(parsed,[3,2]);self.assertEqual(monitor.deferred_direction['owner'],{})

    def test_foreground_required_prepares_once_and_unknown_retry_does_not_reactivate(self):
        old,new=Row(1,'old'),Row(2,'visible pending')
        monitor,_=self.voice_monitor([old,new],{'time':'11:40','content':new.Name,'isnew':False,'new_count':0},[old])
        with patch.object(monitor,'message_direction',side_effect=RuntimeError('message-direction-screen-foreground-required')), \
                patch.object(monitor,'prepare_direction_read') as prepare, \
                patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *a:self.fail('unknown avatar parsed'))}):
            self.assertEqual(monitor.read('owner'),[])
            self.assertEqual(monitor.read('owner'),[])
            self.assertEqual(monitor.read('owner'),[])
            prepare.assert_called_once()
            self.assertNotIn((2,),[key for key,_ in monitor.cursor.rows['owner']])

    def test_visible_pending_is_retried_after_one_preparation_without_extra_input(self):
        old,new=Row(1,'old'),Row(2,'visible pending')
        monitor,_=self.voice_monitor([old,new],{'time':'11:40','content':new.Name,'isnew':False,'new_count':0},[old])
        with patch.object(monitor,'message_direction',side_effect=[RuntimeError('message-direction-screen-foreground-required'),'friend']), \
                patch.object(monitor,'prepare_direction_read') as prepare, \
                patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda c,p:
                    SimpleNamespace(attr='friend',type='text',sender='owner',content=c.Name))}):
            self.assertEqual([r['text'] for r in monitor.read('owner')],[new.Name])
            self.assertEqual(monitor.read('owner'),[])
            prepare.assert_called_once()

    def test_deferred_recycled_runtime_with_changed_content_never_rebinds_as_old_input(self):
        old,unknown,fresh=Row(1,'old'),Row(2,'unknown original'),Row(3,'known tail')
        rows=[old,unknown,fresh]
        monitor,_=self.voice_monitor(rows,{'time':'11:40','content':fresh.Name,'isnew':False,'new_count':0},[old])
        def direction(c):
            if c is unknown:raise RuntimeError('message-direction-unknown')
            return 'self'
        with patch.object(monitor,'message_direction',side_effect=direction), \
                patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *a:self.fail('must not parse'))}):
            self.assertEqual(monitor.read('owner'),[])
            unknown.Name='recycled different body'
            self.assertEqual(monitor.read('owner'),[])
            self.assertEqual(len(monitor.deferred_direction['owner']),1)

    def test_chinese_voice_preview_matches_duration_and_transcribes_once_after_unread_was_cleared(self):
        row=Row(2,'语音3"秒未播放');row.ClassName='mmui::ChatVoiceItemView'
        preview={'time':'23:30','content':'[语音] 3"','isnew':False,'new_count':0}
        monitor,wheel=self.voice_monitor([row],preview)
        msg=SimpleNamespace(attr='friend',type='voice',sender='主人',to_text=lambda:'听得到吗')
        with patch.object(wheel,'WheelDown',wraps=wheel.WheelDown) as moved,patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:msg)}):
            records=monitor.read('owner')
            self.assertEqual(len(records),1)
            self.assertIn('听得到吗',records[0]['text'])
            self.assertEqual(monitor.read('owner'),[])
            moved.assert_not_called()
        self.assertEqual(monitor.recovered_tails,1)

    def test_voice_preview_never_accepts_different_duration_unknown_duration_or_nonvoice_tail(self):
        preview={'time':'23:30','content':'[语音] 3"','isnew':True,'new_count':1}
        for label,klass in [('语音13"秒未播放','mmui::ChatVoiceItemView'),
                            ('语音未播放','mmui::ChatVoiceItemView'),
                            ('[语音] 3"','mmui::ChatTextItemView')]:
            with self.subTest(label=label,klass=klass):
                row=Row(2,label);row.ClassName=klass
                monitor,wheel=self.voice_monitor([row],preview)
                with self.assertRaisesRegex(RuntimeError,'new-message-not-visible'):
                    monitor.read('owner')
                self.assertTrue(monitor.gate.changed('owner',preview))

    def test_matching_old_voice_is_not_evidence_for_new_voice_preview(self):
        old=Row(1,'语音3"秒未播放');old.ClassName='mmui::ChatVoiceItemView'
        preview={'time':'23:30','content':'[语音] 3"','isnew':True,'new_count':1}
        monitor,wheel=self.voice_monitor([old],preview,baseline=[old])
        with self.assertRaisesRegex(RuntimeError,'new-message-not-visible'):
            monitor.read('owner')
        self.assertTrue(monitor.gate.changed('owner',preview))

    def test_voice_preview_matches_only_tail_and_can_recover_after_scrolling_down(self):
        old=Row(1,'语音3"秒未播放');old.ClassName='mmui::ChatVoiceItemView'
        text=Row(2,'later visible text')
        new=Row(3,'语音3"秒已播放');new.ClassName='mmui::ChatVoiceMessageItemView'
        preview={'time':'23:30','content':'member: [语音]3秒','isnew':False,'new_count':0}
        monitor,wheel=self.voice_monitor([old,text],preview,baseline=[old,text])
        visible={'rows':[old,text]}
        monitor.controls=lambda:visible['rows']
        wheel.WheelDown=lambda **kwargs:visible.update(rows=[new])
        msg=SimpleNamespace(attr='friend',type='voice',sender='member',to_text=lambda:'新的语音')
        with patch.object(wheel,'WheelDown',wraps=wheel.WheelDown) as moved,patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:msg)}):
            self.assertEqual(len(monitor.read('owner')),1)
            moved.assert_called_once_with(wheelTimes=8,api=False)

    def test_durationless_voice_preview_still_requires_fresh_voice_tail(self):
        row=Row(2,'语音未播放');row.ClassName='mmui::ChatVoiceItemView'
        preview={'time':'23:30','content':'[语音]','isnew':False,'new_count':0}
        monitor,wheel=self.voice_monitor([row],preview)
        msg=SimpleNamespace(attr='friend',type='voice',sender='主人',to_text=lambda:'短语音')
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:msg)}):
            self.assertEqual(len(monitor.read('owner')),1)

    def test_new_preview_moves_down_to_live_tail_and_recovers_a_virtualized_gap_once(self):
        old=Row(1,'old recipe');new=Row(2,'English please')
        live={'value':False}
        wheel=SimpleNamespace(WheelDown=lambda **kwargs:live.update(value=True))
        monitor=MainWindowMonitor(SimpleNamespace(_api=SimpleNamespace(_chat_api=SimpleNamespace(msgbox=wheel))),{'group':'group'})
        monitor.cursor.seed('group',[old]);monitor.select=lambda name:None;monitor.current=lambda:'group'
        monitor.controls=lambda:[new] if live['value'] else [old]
        preview={'time':'19:04','content':'[3条]\n[有人@我]\nkaren: English please','isnew':True,'new_count':1}
        monitor.sessions=lambda:{'group':preview}
        message=SimpleNamespace(attr='friend',type='text',sender='小明',content='English please')
        with patch.object(wheel,'WheelDown',wraps=wheel.WheelDown) as moved,patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:message)}):
            records=monitor.read('group')
            self.assertEqual([r['text'] for r in records],['English please'])
            self.assertEqual(monitor.read('group'),[])
            moved.assert_called_once_with(wheelTimes=8,api=False)
        self.assertEqual(monitor.recovered_tails,1)

    def test_unrendered_new_preview_is_never_acknowledged_as_received(self):
        old=Row(1,'old recipe')
        wheel=SimpleNamespace(WheelDown=lambda **kwargs:None)
        monitor=MainWindowMonitor(SimpleNamespace(_api=SimpleNamespace(_chat_api=SimpleNamespace(msgbox=wheel))),{'group':'group'})
        monitor.cursor.seed('group',[old]);monitor.select=lambda name:None;monitor.current=lambda:'group'
        monitor.controls=lambda:[old]
        preview={'time':'19:04','content':'小明: English please','isnew':True,'new_count':1}
        monitor.sessions=lambda:{'group':preview}
        with self.assertRaisesRegex(RuntimeError,'new-message-not-visible'):monitor.read('group')
        self.assertTrue(monitor.gate.changed('group',preview))

    def test_sent_bubbles_can_replace_the_entire_old_view_without_hiding_a_member_message_between_them(self):
        monitor=MainWindowMonitor(SimpleNamespace(_api=SimpleNamespace(control=object(),
            _chat_api=SimpleNamespace(msgbox=object()))),{'group':'group'})
        monitor.cursor.seed('group',[Row(1,'old incoming')])
        first, incoming, last=Row(2,'our first bubble'),Row(3,'member during send'),Row(4,'our last bubble')
        monitor.current=lambda:'group'
        monitor.select=lambda name:None
        monitor.controls=lambda:[first,incoming,last]
        monitor.sessions=lambda:{}
        parsed=[]
        def parse(control,parent):
            parsed.append(control.key)
            return SimpleNamespace(attr='friend' if control.key==3 else 'self',type='text',sender='member' if control.key==3 else 'self',content=control.Name)
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=parse)}), \
                patch.object(monitor,'message_direction',side_effect=lambda c:'friend' if c.key==3 else 'self'):
            monitor.note_outbound('group')
            records=monitor.read('group')
            self.assertEqual([r['text'] for r in records],['member during send'])
            self.assertEqual(monitor.read('group'),[])
            self.assertEqual(parsed,[3]) # Own bubbles never construct SDK messages.

    def test_new_input_never_reparses_or_reattaches_old_context_rows(self):
        old, own, media, bad, new = [Row(i,str(i)) for i in range(1,6)]
        media.ClassName = 'mmui::ChatImageItemView'
        monitor=MainWindowMonitor(SimpleNamespace(_api=SimpleNamespace(_chat_api=object())),{'group':'group'})
        monitor.cursor.seed('group',[old,own,media,bad])
        monitor.select=lambda name:None
        monitor.controls=lambda:[old,own,media,bad,new]
        monitor.sessions=lambda:{}
        parsed=[]
        def parse(control,parent):
            parsed.append(control.key)
            if control.key==4:raise RuntimeError('old row unavailable')
            return SimpleNamespace(attr='self' if control.key==2 else 'friend',type='text',sender='bot' if control.key==2 else 'member',content='old reply' if control.key==2 else 'text '+str(control.key))
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=parse)}):
            records=monitor.read('group')
        self.assertEqual([r['text'] for r in records],['text 5'])
        self.assertEqual(monitor.histories,{})
        self.assertEqual(parsed,[5])
        self.assertNotIn(3,parsed)

    def test_qt_recreated_and_recycled_ids_do_not_replay_the_same_messages(self):
        cursor=MessageCursor()
        cursor.seed('owner',[Row(1,'first'),Row(2,'second'),Row(3,'third')])
        self.assertEqual(cursor.pending('owner',[Row(2,'first'),Row(8,'second'),Row(9,'third')]),[])

    def test_newly_rendered_older_row_above_a_known_tail_is_not_a_new_request(self):
        cursor=MessageCursor()
        cursor.seed('owner',[Row(1,'first'),Row(2,'latest')])
        self.assertEqual(cursor.pending('owner',[Row(20,'older history'),Row(21,'first'),Row(22,'latest')]),[])

    def test_scrolling_back_to_an_accepted_message_never_creates_a_new_request(self):
        cursor=MessageCursor()
        cursor.seed('owner',[Row(1,'earlier'),Row(2,'middle')])
        cursor.seed('owner',[Row(3,'latest'),Row(4,'answer')])
        self.assertEqual(cursor.pending('owner',[Row(9,'earlier'),Row(10,'middle')]),[])

    def test_chat_switch_waits_for_header_without_reclicking(self):
        wx = SimpleNamespace(ChatWith=lambda *args, **kwargs: None)
        monitor = MainWindowMonitor(wx, {'group':'group'})
        with patch.object(wx, 'ChatWith') as switch, patch.object(monitor, 'current', side_effect=['other','other','group','group']), patch('message_watch.time.sleep'):
            monitor.select('group')
        switch.assert_called_once_with('group', exact=True)

    def test_wrong_chat_never_passes_after_timeout(self):
        wx = SimpleNamespace(ChatWith=lambda *args, **kwargs: None)
        monitor = MainWindowMonitor(wx, {'group':'group'})
        with patch.object(monitor, 'current', return_value='other'), patch('message_watch.time.monotonic', side_effect=[0,0,3]), patch('message_watch.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'target-mismatch'):
                monitor.select('group')

    def test_reused_control_id_with_new_text_is_fresh(self):
        cursor=MessageCursor()
        cursor.seed('owner',[Row(1,'old'),Row(2,'before')])
        new=Row(2,'new message')
        self.assertEqual(cursor.pending('owner',[Row(1,'old'),new]),[new])

    def test_receipt_ids_do_not_collide_across_worker_restarts(self):
        self.assertNotEqual(event_key('boot1','owner','same UIA id'),event_key('boot2','owner','same UIA id'))
        self.assertEqual(event_key('boot1','owner','id'),event_key('boot1','owner','id'))

    def test_idle_and_new_repeated_text(self):
        cursor = MessageCursor()
        rows = [Row(1, 'a'), Row(2, 'a')]
        cursor.seed('group', rows)
        self.assertEqual(cursor.pending('group', rows), [])
        extra = Row(3, 'a')
        self.assertEqual(cursor.pending('group', rows + [extra]), [extra])

    def test_switch_recreates_controls_without_replaying_history(self):
        cursor = MessageCursor()
        cursor.seed('private', [Row(1, 'old'), Row(2, 'last')])
        cursor.seed('group', [Row(3, 'group')])
        new = Row(12, 'new')
        self.assertEqual(cursor.pending('private', [Row(10, 'old'), Row(11, 'last'), new]), [new])

    def test_history_gap_does_not_replay_old_messages(self):
        cursor = MessageCursor()
        cursor.seed('private', [Row(1, 'last')])
        cursor.seed('group', [])
        with self.assertRaisesRegex(RuntimeError, 'history-gap'):
            cursor.pending('private', [Row(2, 'unknown')])

    def test_repeat_arriving_while_other_chat_selected_is_not_discarded(self):
        cursor = MessageCursor()
        cursor.seed('private', [Row(1, 'same')])
        cursor.seed('group', [])
        repeated = Row(3, 'same')
        self.assertEqual(cursor.pending('private', [Row(2, 'same'), repeated]), [repeated])

    def test_gate_sees_preview_changes_even_if_auto_marked_read(self):
        gate = SessionGate()
        old = dict(time='12:00', content='a', isnew=False, new_count=0)
        gate.acknowledge('owner', old)
        self.assertFalse(gate.changed('owner', old))
        self.assertTrue(gate.changed('owner', {**old, 'content': 'b'}))
        unread = {**old, 'isnew': True, 'new_count': 2}
        gate.acknowledge('group', unread)
        self.assertFalse(gate.changed('group', unread))

    def test_preview_arriving_during_parse_remains_pending(self):
        monitor = MainWindowMonitor(SimpleNamespace(_api=SimpleNamespace(_chat_api=object())), {'owner':'friend'})
        old, new = Row(1, 'old'), Row(2, 'new')
        monitor.cursor.seed('owner', [old])
        preview = {'time':'12:00','content':'new','isnew':False,'new_count':0}
        monitor.select = lambda name: None
        monitor.controls = lambda: [old,new]
        monitor.sessions = lambda: {'owner':dict(preview)}
        def parse(control, parent):
            preview['content'] = 'another arrived while parsing'
            return SimpleNamespace(id='new',attr='friend',type='text',content='new',sender='owner')
        with patch.dict(sys.modules, {'wxauto4.msgs.msg':SimpleNamespace(parse_msg=parse)}):
            records=monitor.read('owner')
        self.assertEqual(len(records),1)
        self.assertTrue(monitor.gate.changed('owner',preview))

    def test_same_runtime_text_replaced_during_parse_is_not_accepted_before_reading_it(self):
        old,row=Row(1,'old'),Row(2,'message-A')
        preview={'time':'12:00','content':'message-A','isnew':False,'new_count':0}
        monitor,_=self.voice_monitor([old,row],preview,baseline=[old])
        parsed=[]
        def parse(control,parent):
            content=control.Name;parsed.append(content)
            if content=='message-A':
                control.Name='message-B';preview.update(time='12:01',content='message-B')
            return SimpleNamespace(attr='friend',type='text',sender='owner',content=content)
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=parse)}):
            self.assertEqual([r['text'] for r in monitor.read('owner')],['message-A'])
            self.assertNotIn(__import__('message_watch').signature(row),monitor.cursor.accepted['owner'])
            self.assertEqual([r['text'] for r in monitor.read('owner')],['message-B'])
            self.assertEqual(monitor.read('owner'),[])
        self.assertEqual(parsed,['message-A','message-B'])

class VoiceTests(ReadFixtures):
    def monitor(self, row):
        monitor = MainWindowMonitor(SimpleNamespace(_api=SimpleNamespace(_chat_api=object())),
                                    {'owner':'friend'}, receive_voice=True)
        monitor.cursor.seed('owner', [Row(1,'old')])
        monitor.select = lambda name: None
        monitor.controls = lambda: [Row(1,'old'),row]
        monitor.sessions = lambda: {}
        return monitor

    def test_native_transcript_enters_same_chat_once_even_if_transcription_changes_row(self):
        row=Row(2,'voice');row.ClassName='mmui::ChatVoiceItemView'
        monitor=self.monitor(row)
        def to_text():
            row.Name='voice converted'
            return '你好，小鲸'
        msg=SimpleNamespace(attr='friend',type='voice',sender='主人',to_text=to_text)
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:msg)}):
            records=monitor.read('owner')
            self.assertEqual(len(records),1)
            self.assertIn('你好，小鲸',records[0]['text'])
            self.assertEqual(records[0]['mediaType'],'voice')
            self.assertEqual(monitor.read('owner'),[])

    def test_failed_transcription_is_unknown_not_invented_speech(self):
        row=Row(2,'voice');row.ClassName='mmui::ChatVoiceItemView'
        monitor=self.monitor(row)
        def fail(): raise RuntimeError('not supported')
        msg=SimpleNamespace(attr='friend',type='voice',sender='主人',to_text=fail)
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:msg)}):
            records=monitor.read('owner')
            self.assertIn('内容未知',records[0]['text'])
            self.assertEqual(monitor.read('owner'),[])

    def test_transcription_never_refreshes_a_sibling_reused_runtime(self):
        old,voice=Row(1,'old'),Row(2,'voice')
        voice.ClassName='mmui::ChatVoiceItemView';monitor=self.monitor(voice)
        visible={'rows':[old,voice]}
        monitor.controls=lambda:visible['rows']
        def transcribe():
            voice.Name='voice converted';old.Name='new-text-on-reused-row'
            # Recycler reuses the sibling as the new live tail. The captured
            # list stays immutable; the next poll gets its new ordering.
            visible['rows']=[voice,old]
            return 'voice words'
        def parse(row,parent):
            return SimpleNamespace(attr='friend',type='voice' if row is voice else 'text',sender='owner',
                content=row.Name,to_text=transcribe)
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=parse)}):
            first=monitor.read('owner');self.assertEqual(len(first),1);self.assertEqual(first[0]['mediaType'],'voice')
            self.assertNotIn(__import__('message_watch').signature(old),monitor.cursor.accepted['owner'])
            second=monitor.read('owner');self.assertEqual([r['text'] for r in second],['new-text-on-reused-row'])
            self.assertEqual(monitor.read('owner'),[])

    def test_voice_refresh_rejects_changed_runtime_or_changed_class(self):
        for change in ('runtime','class'):
            with self.subTest(change=change):
                row=Row(2,'voice');row.ClassName='mmui::ChatVoiceItemView';monitor=self.monitor(row)
                def transcribe():
                    row.Name='unprocessed-new-row'
                    if change=='runtime':row.key=3
                    else:row.ClassName='mmui::ChatTextItemView'
                    return 'voice words'
                msg=SimpleNamespace(attr='friend',type='voice',sender='owner',to_text=transcribe)
                with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:msg)}):monitor.read('owner')
                self.assertNotIn(__import__('message_watch').signature(row),monitor.cursor.accepted['owner'])

class PrivatePeerParserTests(unittest.TestCase):
    def test_bound_private_peer_uses_type_only_parser_without_direction_or_profile_lookup(self):
        row=Row(7,'private incoming');parent=object();calls=[]
        monitor=MainWindowMonitor(SimpleNamespace(_api=SimpleNamespace(_chat_api=parent)),{'owner':'friend','group':'group'})
        monitor.current=lambda:'owner'
        def typed(control,chat,attr,nickname):
            calls.append((control,chat,attr,nickname))
            return SimpleNamespace(attr='friend',type='text',sender=nickname,content=control.Name)
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg_type=typed,
                parse_msg=lambda *a:self.fail('avatar direction parser called'))}):
            result=monitor.parse_private(row,'owner')
        self.assertEqual(result.sender,'owner')
        self.assertEqual(calls,[(row,parent,'Friend','owner')])

    def test_group_or_changed_chat_is_refused_before_any_type_construction(self):
        monitor=MainWindowMonitor(SimpleNamespace(),{'owner':'friend','group':'group'})
        for name,current in [('group','group'),('owner','group'),('stranger','stranger')]:
            monitor.current=lambda:current
            with self.subTest(name=name,current=current),self.assertRaisesRegex(RuntimeError,'target-mismatch'):
                monitor.parse_private(Row(1,'test'),name)

if __name__ == '__main__': unittest.main()

class NativeGroupTextObservationTests(ReadFixtures):
    monitor = StartupUnreadTests.monitor
    info = staticmethod(StartupUnreadTests.info)

    def authority(self,mode='native'):
        return {'version':1,'mode':mode,'nativeChatKey':'wechat:group_fixture@chatroom',
                'authorityEpoch':'a'*32,'accountId':'account','target':'group'}

    def configured(self,mode):
        row=Row(98,'real-shaped text')
        monitor,state=self.monitor({'group':[row]},{'group':self.info(row.Name,1)},original='group')
        monitor.native_group_text_authority=lambda name:self.authority(mode)
        monitor.cursor.seed('group',[])
        return monitor,state,row

    def test_committed_native_group_text_never_calls_direction_or_avatar_parser(self):
        monitor,state,row=self.configured('native')
        with patch.object(monitor,'message_direction',side_effect=AssertionError('direction called')), \
                patch.object(monitor,'parse_incoming',side_effect=AssertionError('avatar parser called')):
            records=monitor.read('group')
            self.assertEqual(len(records),1)
            self.assertEqual(set(records[0]),{'observation','id','nativeChatKey','authorityEpoch'})
            self.assertEqual(records[0]['observation'],'native-group-text')
            self.assertNotIn(row.Name,str(records))
            self.assertEqual(monitor.read('group'),[])
            monitor.note_outbound('group')

    def test_startup_native_text_qualification_skips_sdk(self):
        monitor,state,row=self.configured('native')
        with patch.object(monitor,'message_direction',side_effect=AssertionError('direction called')), \
                patch.object(monitor,'parse_incoming',side_effect=AssertionError('avatar parser called')):
            self.assertEqual(monitor.startup_tail('group',[row],self.info(row.Name,1)),[row])

    def test_blocked_text_retries_without_ack_or_consumption(self):
        monitor,state,row=self.configured('blocked')
        with patch.object(monitor,'message_direction',side_effect=AssertionError('direction called')), \
                patch.object(monitor,'parse_incoming',side_effect=AssertionError('avatar parser called')):
            records=monitor.read('group');again=monitor.read('group')
        self.assertEqual(records[0]['observation'],'native-group-text-blocked')
        self.assertEqual(again[0]['observation'],'native-group-text-blocked')
        self.assertNotIn('group',monitor.gate.handled)
        self.assertEqual(monitor.cursor.rows['group'],[])
        self.assertTrue(monitor.deferred_direction['group'])

    def test_preparing_and_default_keep_actual_sdk_text_envelope(self):
        for callback in (None,lambda name:self.authority('preparing')):
            monitor,state,row=self.configured('ui');monitor.native_group_text_authority=callback
            message=SimpleNamespace(attr='friend',type='text',content=row.Name,sender='actual sender')
            with patch.object(monitor,'parse_incoming',return_value=message) as parser:
                result=monitor.read('group')
            self.assertEqual(result[0]['text'],row.Name);self.assertEqual(parser.call_count,1)

    def test_bad_callback_native_identity_fails_closed_but_owner_ignores_it(self):
        monitor,state,row=self.configured('native')
        monitor.native_group_text_authority=lambda name:{**self.authority(),'authorityEpoch':'invalid'}
        with patch.object(monitor,'parse_incoming',side_effect=AssertionError('avatar parser called')):
            self.assertEqual(monitor.read('group')[0]['observation'],'native-group-text-blocked')
        monitor.routes['owner']='friend'
        self.assertEqual(monitor.group_text_state('owner'),{'mode':'ui'})

    def test_blocked_startup_does_not_seed_text_but_preserves_other_media_baseline(self):
        from message_watch import signature
        monitor,state,row=self.configured('blocked');monitor.recover_startup_unread=False
        image=Row(99,'图片');image.ClassName='mmui::ChatImageItemView'
        state['rows']['group'].append(image)
        monitor.initialize()
        self.assertNotIn(signature(row),monitor.cursor.accepted.get('group',set()))
        self.assertIn(signature(image),monitor.cursor.accepted.get('group',set()))
        self.assertNotIn('group',monitor.gate.handled)

    def test_independent_bound_monitor_forwards_same_optional_authority(self):
        from independent_windows import BoundWindowMonitor
        binding=SimpleNamespace(chat=SimpleNamespace(),target='group',kind='group')
        monitor=BoundWindowMonitor(binding,native_group_text_authority=lambda target:self.authority())
        self.assertEqual(monitor.group_text_state('group')['mode'],'native')
        legacy=BoundWindowMonitor(binding)
        self.assertEqual(legacy.group_text_state('group'),{'mode':'ui'})
