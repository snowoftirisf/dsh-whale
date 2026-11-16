"""Keep one reply's bubbles together on the single WeChat UI worker."""
import time


class DeliveryWindow:
    def __init__(self):
        self.owner = None
        self.deadline = 0

    def active(self):
        if time.monotonic() >= self.deadline:
            self.owner = None
        return self.owner

    def begin(self, reply_id, target):
        if self.active() is not None:
            raise RuntimeError('delivery-window-busy')
        self.owner = (reply_id, target)
        self.deadline = time.monotonic() + 120

    def check(self, reply_id, target):
        if self.active() != (reply_id, target):
            raise RuntimeError('delivery-window-mismatch')
        self.deadline = time.monotonic() + 120

    def end(self, reply_id, target):
        self.check(reply_id, target)
        self.owner = None
