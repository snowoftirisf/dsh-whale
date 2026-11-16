import unittest
from passive_read import read_chat_snapshot, without_focus

class PassiveReadTests(unittest.TestCase):
    def test_lazy_message_fields_do_not_steal_focus_and_send_restores_focus(self):
        class Control:
            def SetFocus(self):
                raise AssertionError('desktop focus was changed')
        control = Control()
        original = Control.SetFocus
        class Message:
            def __getattr__(self, name):
                control.SetFocus()
                return {'id':'1','attr':'friend','type':'text','content':'你好','sender':'甲'}[name]
        class Chat:
            def ChatInfo(self):
                control.SetFocus()
                return {'chat_name':'群','chat_type':'group'}
            def GetAllMessage(self):
                control.SetFocus()
                return [Message()]
        info, records, suppressed = read_chat_snapshot(Chat(), Control)
        self.assertEqual(records[0]['content'], '你好')
        self.assertGreater(suppressed, 2)
        self.assertIs(Control.SetFocus, original)
        with self.assertRaisesRegex(AssertionError, 'desktop focus'):
            control.SetFocus()

    def test_original_focus_function_restored_after_read_error(self):
        class Control:
            def SetFocus(self): return False
        original = Control.SetFocus
        with self.assertRaises(ValueError):
            with without_focus(Control):
                raise ValueError('read error')
        self.assertIs(Control.SetFocus, original)
