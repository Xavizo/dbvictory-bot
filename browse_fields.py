"""Track the one tile explicitly browsed by the player in the current login."""
from dataclasses import dataclass


@dataclass(frozen=True)
class OpenField:
    position: tuple
    window: int


class BrowseFields:
    def __init__(self):
        self.pending = None
        self.opened = None
        self.quiet_until = 0.
        self.field_windows = set()

    def clear(self):
        self.pending = self.opened = None
        self.field_windows.clear()

    def navigating(self, number):
        self.pending = None
        if self.opened and (number is None or self.opened.window == number):
            self.opened = None

    def requested(self, position, now):
        previous = self.pending
        self.pending = self.opened = None
        # Replies have no request ID. Two outstanding clicks cannot be paired.
        if now < self.quiet_until or (previous and now - previous[1] <= 2):
            self.quiet_until = now + 2
        else:
            self.pending = (position, now)

    def window_changed(self, number, is_field, received):
        if is_field:
            self.field_windows.add(number)
        else:
            self.field_windows.discard(number)
        if self.opened and self.opened.window == number:
            self.opened = None
        if not is_field or not self.pending:
            return
        position, sent = self.pending
        if received < sent:
            return
        self.pending = None
        if received - sent <= 2:
            self.opened = OpenField(position, number)


def field_window_header(data, pos):
    """Match only a length-prefixed, complete window title at the open header."""
    if pos + 3 > len(data) or data[pos] > 15:
        return False
    for start in (pos + 3, pos + 4):  # item ID, optionally followed by its marker
        length = int.from_bytes(data[start:start + 2], "little")
        end = start + 2 + length
        if end + 2 <= len(data) and data[start + 2:end].lower() in (b"browse field", b"browse fields"):
            return True
    return False


def field_slot(layers, visible, selected):
    """Resolve the displayed client slot from both views, including reversed lists."""
    if selected not in layers:
        return None
    ids = tuple(entry[1] for entry in layers)
    index = layers.index(selected)
    candidates = set()
    if visible == ids:
        candidates.add(index)
    if visible == ids[::-1]:
        candidates.add(len(ids) - 1 - index)
    if len(candidates) == 1:
        return candidates.pop()
    # Some fixed objects are shown in only one view. A unique ID is unambiguous
    # once the window has been associated with the exact field.
    if ids.count(selected[1]) == visible.count(selected[1]) == 1:
        return visible.index(selected[1])
    return None
