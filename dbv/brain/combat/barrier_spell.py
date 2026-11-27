"""Renew the character's Barrier spell using the observed effect and spell cooldown."""
import math
import random
import time

from dbv.client_bridge.command_delivery import attempted, confirm
from dbv.brain.state.item_observations import observed_inputs, wait_for_inputs
from dbv.brain.state.session_lifetime import SessionLease
from dbv.brain.combat.speed_up import fresh

NAME = "barrier_spell"
BARRIER_ICON = 0x10
DEFAULT_DURATION = 200
DEFAULTS = {"duration": DEFAULT_DURATION, "min_before": 5, "max_before": 30}
INPUTS = ("effects", "health")
ACK_WAIT = 3


def validate_config(config):
    if not isinstance(config, dict) or set(config) - (DEFAULTS.keys() | {"enabled"}):
        raise ValueError("Nieprawidłowe ustawienia Barrier.")
    result = {**DEFAULTS, **config}
    for key in DEFAULTS:
        value = result[key]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Podaj czasy Barrier w sekundach.")
    if not (2 <= result["duration"] <= 3600 and
            0 <= result["min_before"] <= result["max_before"] <= result["duration"] - 2):
        raise ValueError("Wyprzedzenie musi mieścić się między 0 a czasem bariery pomniejszonym o 2 s.")
    return result


class BarrierScheduler:
    def __init__(self, draw=random.uniform):
        self.draw = draw
        self.active = None
        self.due = None
        self.started_at = None
        self.last_ack = 0
        self.pending = None
        self.receipt = None
        self.failures = 0
        self.retry_at = 0
        self.status = "Czekam na odczyt bariery"

    def observe(self, sample, config, ack, now):
        if not fresh(sample, now):
            self.status = "Czekam na aktualny stan żywej postaci"
            return False
        active = bool(sample["conditions"] & BARRIER_ICON)
        onset = active and self.active is False
        expired = not active and self.active is True
        self.active = active
        # A cooldown can arrive just before the first active sample. Consume it
        # only once the icon is present, also for a renewal with no icon change.
        new_ack = active and self.last_ack < ack <= now and now - ack < config["duration"]
        if expired:
            self.due = None
            self.last_ack = max(self.last_ack, ack)
            self.failures = 0
            self.retry_at = 0
        if not active and self.pending is None and self.last_ack < ack <= now and now - ack < ACK_WAIT:
            self.status = "Czekam na ikonę po rzuceniu Barrier"
            return False
        if new_ack or (onset and self.pending is not None):
            started = ack if new_ack else now
            if new_ack:
                self.last_ack = ack
            # A delayed cooldown for the same onset must not draw a second time.
            if new_ack and self.pending is None and self.due is not None and self.started_at - 1 <= started <= self.started_at + 1:
                return False
            self.started_at = started
            self.due = started + config["duration"] - self.draw(config["min_before"], config["max_before"])
            if self.pending is not None and 0 <= started - self.pending <= ACK_WAIT:
                confirm(self.receipt, "barrier_observed")
            self.pending = self.receipt = None
            self.failures = 0
            self.retry_at = 0
        if self.pending is not None:
            if now - self.pending < ACK_WAIT:
                self.status = "Czekam na potwierdzenie Barrier"
                return False
            self.pending = self.receipt = None
            # With an active icon an unconfirmed early renewal may have worked.
            # Wait for a new cooldown or actual expiry instead of recasting.
            if active:
                self.due = None
            else:
                self.failures += 1
                self.retry_at = now + min(15, 2 * self.failures)
        if active and self.due is None:
            self.status = "Bariera aktywna · czekam na zmianę efektu"
            return False
        if active and now < self.due:
            self.status = f"Bariera aktywna · odnowienie za {math.ceil(self.due - now)} s"
            return False
        if now < self.retry_at:
            self.status = "Czekam na kolejną próbę Barrier"
            return False
        if sample.get("mana") == 0:
            self.status = "Czekam na Ki"
            return False
        self.status = "Odnowienie bariery" if active else "Rzucanie Barrier"
        return True

    def sent(self, receipt, now):
        self.pending = now
        self.receipt = receipt
        self.status = "Czekam na potwierdzenie Barrier"


async def run_barrier(bot):
    state = bot.state
    game = state.game_state
    lease = SessionLease(state)
    scheduler = state.barrier_scheduler = BarrierScheduler()
    try:
        while lease.valid() and not state.automation_paused:
            cursor = observed_inputs(game, INPUTS)
            config = validate_config(state.settings.get("actions", {}).get(NAME, {}))
            if not config.get("enabled"):
                break
            ack = getattr(game, "barrier_cast_at", 0)
            if scheduler.observe(game.client_snapshot, config, ack, time.monotonic()):
                def ready():
                    sample = game.client_snapshot
                    return (lease.valid() and not state.automation_paused
                            and state.settings.get("actions", {}).get(NAME, {}).get("enabled") is True
                            and fresh(sample, time.monotonic()) and sample.get("mana") != 0
                            and bool(sample["conditions"] & BARRIER_ICON) == scheduler.active
                            and getattr(game, "barrier_cast_at", 0) == ack)

                if ready():
                    started = time.monotonic()
                    receipt = await bot.say("barrier", guard=ready)
                    if attempted(receipt):
                        scheduler.sent(receipt, started)
                    else:
                        scheduler.retry_at = started + 1
            await wait_for_inputs(bot, INPUTS, cursor, .2)
    finally:
        scheduler.status = "Wyłączony"
