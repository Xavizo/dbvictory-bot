"""Transport receipts. Dispatch is not proof that the server executed a command."""
import asyncio
import time

ATTEMPTED_STATES = {'dispatched', 'unknown'}


class CommandReceipt:
    def __init__(self, sequence, session, target, opcode, ttl=.5):
        self.id = sequence
        self.session = session
        self.target = target
        self.opcode = opcode
        self.created = time.monotonic()
        self.expires = self.created + ttl
        self.status = 'queued'
        self.reason = None
        self.dispatched_at = None
        self.resolved_at = None
        self.confirmed_at = None
        self.write_started = False
        self.effect = 'pending'
        self.evidence = None
        self._done = asyncio.get_running_loop().create_future()

    @property
    def attempted(self):
        return self.status in ATTEMPTED_STATES

    def finish(self, status, reason=None):
        if self.status != 'queued':
            return
        self.status, self.reason = status, reason
        self.resolved_at = time.monotonic()
        if status in ATTEMPTED_STATES:
            self.dispatched_at = self.resolved_at
        else:
            self.effect = 'not_sent'
        if not self._done.done():
            self._done.set_result(self)

    async def wait(self):
        try:
            return await asyncio.wait_for(asyncio.shield(self._done), max(0,self.expires-time.monotonic())+1.1)
        except asyncio.TimeoutError:
            self.finish('unknown' if self.write_started else 'expired', 'dispatch_timeout')
            return self
        except asyncio.CancelledError:
            self.finish('unknown' if self.write_started else 'cancelled', 'owner_cancelled')
            raise

    def confirm(self, evidence):
        if self.attempted and self.confirmed_at is None:
            self.effect, self.evidence = 'confirmed', evidence
            self.confirmed_at = time.monotonic()

    def snapshot(self):
        return dict(id=self.id,session=self.session,target=self.target,opcode=self.opcode,
                    status=self.status,reason=self.reason,effect=self.effect,evidence=self.evidence,
                    age_ms=round((time.monotonic()-self.created)*1000),
                    delivery_result_ms=None if self.resolved_at is None else round((self.resolved_at-self.created)*1000),
                    effect_after_result_ms=None if self.confirmed_at is None else round((self.confirmed_at-self.dispatched_at)*1000))


class QueuedCommand(tuple):
    """Retain the legacy tuple shape for old action integrations."""
    def __new__(cls, target, payload, guard, receipt):
        value=super().__new__(cls,(target,payload,guard))
        value.receipt=receipt
        return value


async def delivery(value):
    """Older transport adapters return None; current OTProxy always returns a receipt."""
    return await value.wait() if isinstance(value,CommandReceipt) else None


def attempted(receipt):
    return receipt is None or receipt.attempted


def confirm(receipt, evidence):
    if isinstance(receipt,CommandReceipt):receipt.confirm(evidence)
