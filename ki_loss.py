"""Recognition of a single observed Ki loss for alarms and healing rules."""
from typing import NamedTuple

from client_player_state import FRESH_SECONDS
from passive_map import WIDTH, HEIGHT

KI_DROP_WINDOW = 1.5
KI_HIT_GRACE = 1.


class KiDrop(NamedTuple):
    at: float
    elapsed: float
    before: float
    after: float


class KiDropDetector:
    def __init__(self):
        self.high = None

    def observe(self, gs, now, wall):
        previous = self.high
        self.high = None
        stamp, mana, maximum = gs.stats_updated_at, gs.mana, gs.max_mana
        position = tuple(gs.position)
        if (not 0 <= wall - stamp <= KI_DROP_WINDOW or gs.hp <= 0 or maximum <= 0
                or not 0 <= mana <= maximum or len(position) != 3):
            return None
        if mana * 100 >= maximum * 90:
            self.high = (stamp, mana, maximum, position, gs.ki_revision)
            return None
        if previous is None or mana * 100 > maximum * 5:
            return None
        before, high, old_max, origin, revision = previous
        max_gap = FRESH_SECONDS if gs.client_state_required else KI_DROP_WINDOW
        if (gs.ki_revision != revision + 1 or not 0 <= stamp - before <= max_gap
                or old_max != maximum or origin[2] != position[2]
                or abs(origin[0] - position[0]) > WIDTH // 2 or abs(origin[1] - position[1]) > HEIGHT // 2):
            return None
        return KiDrop(now - (wall - stamp), stamp - before, high / maximum, mana / maximum)
