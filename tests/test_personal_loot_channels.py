"""Channel provenance and read boundaries for the Hunter Knife Loot stream."""
import struct

import pytest

from dbv.brain.state.game_state import GameState, scan_packet
from dbv.client_bridge.packet_observer import apply_scan, detach_state
from dbv.client_bridge.protocol import ServerOpcode


FURY = "[Fury Mutant Fire Elemental]: nothing."


def string(value):
    data = value.encode("utf-8")
    return struct.pack("<H", len(data)) + data


def roster(*rows):
    return bytes([ServerOpcode.CHANNELS, len(rows)]) + b"".join(
        struct.pack("<H", channel) + string(name) for channel, name in rows
    )


def channel_message(channel, text, *, sender="", level=0):
    return (bytes([ServerOpcode.TALK]) + struct.pack("<I", 0) + string(sender)
            + struct.pack("<HBH", level, 7, channel) + string(text))


def receive(game, data, at=20.):
    scan = detach_state(game, 0)
    scan.received_at = at
    complete = scan_packet(data, scan.state)
    apply_scan(game, scan)
    return complete


@pytest.mark.parametrize("loot_id", [0, 41, 6000])
def test_complete_roster_then_four_identical_personal_entries_in_one_batch(loot_id):
    game = GameState()
    payload = roster((17, "Trade"), (loot_id, "Loot")) + channel_message(loot_id, FURY) * 4
    assert receive(game, payload)
    assert game.personal_loot_channel == loot_id
    assert game.knife_loot_count == 4


def test_only_own_party_shield_disables_solo_xp_evidence():
    game = GameState()
    game.player_id = 0x10000005
    def shield(cid, value):
        return struct.pack("<BIB", ServerOpcode.CREATURE_PARTY, cid, value)
    assert receive(game, shield(game.player_id + 1, 3))
    assert game.player_party_shield == 0
    assert receive(game, shield(game.player_id, 3))
    assert game.player_party_shield == 3
    assert receive(game, shield(game.player_id, 0))
    assert game.player_party_shield == 0


def test_unrecognized_system_chat_does_not_claim_a_body_or_hide_later_loot():
    game = GameState()
    assert receive(game, channel_message(2, FURY))
    assert game.knife_loot_count == 0
    payload = (roster((2, "Trade"), (3, "Loot")) + channel_message(2, FURY)
               + channel_message(3, FURY, sender="Player", level=1)
               + channel_message(3, FURY, sender="[Fury Mutant Fire Elemental]")
               + channel_message(3, FURY))
    assert receive(game, payload)
    assert game.knife_loot_count == 1


def test_roster_and_open_channel_keep_or_revoke_previously_named_identity():
    game = GameState()
    assert receive(game, roster((3, "Loot")))
    assert receive(game, roster((7, "Help")))  # A catalog can omit an open channel.
    assert game.personal_loot_channel == 3
    assert receive(game, roster((3, "Trade")) + channel_message(3, FURY))
    assert game.personal_loot_channel is None and game.knife_loot_count == 0

    opening = bytes([ServerOpcode.OPEN_CHANNEL]) + struct.pack("<H", 9) + string("Loot")
    assert receive(game, opening)
    assert game.personal_loot_channel == 9
    assert receive(game, bytes([ServerOpcode.CLOSE_CHANNEL]) + struct.pack("<H", 9))
    assert receive(game, channel_message(9, FURY))
    assert game.personal_loot_channel is None and game.knife_loot_count == 0


@pytest.mark.parametrize("invalid", [
    bytes([ServerOpcode.CHANNELS]),
    roster((3, "Loot"), (4, "Other"))[:-1],
    roster((3, "Loot"), (3, "Trade")),
    roster((3, "Loot"), (4, " ")),
])
def test_invalid_channel_roster_cannot_partially_establish_an_identity(invalid):
    game = GameState()
    assert not receive(game, invalid)
    assert game.personal_loot_channel is None
    assert receive(game, channel_message(3, FURY))
    assert game.knife_loot_count == 0


def test_ambiguous_roster_cannot_claim_either_channels_messages():
    game = GameState()
    assert receive(game, roster((3, "Loot")))
    assert receive(game, roster((3, "Loot"), (7, "Loot"))
                   + channel_message(3, FURY) + channel_message(7, FURY))
    assert game.personal_loot_channel is None and game.knife_loot_count == 0


def test_open_channel_with_unverified_tail_never_reads_the_tail_as_loot():
    game = GameState()
    opening = bytes([ServerOpcode.OPEN_CHANNEL]) + struct.pack("<H", 9) + string("Loot")
    assert not receive(game, opening + channel_message(9, FURY))
    assert game.knife_loot_count == 0
    assert receive(game, channel_message(9, FURY))
    assert game.knife_loot_count == 1
