"""Packet diagnostics: log every packet the client sends.

Each line has the opcode, its name and the size. Packet bytes may contain
chat and other private data, so they are written only when the bot was
started with DBV_ALLOW_PACKET_CAPTURE=1. The log goes to sniff_log.txt in
the bot folder and is started fresh each time the action is enabled.
"""
import itertools
import os
import struct

from dbv.client_bridge.protocol import ClientOpcode
from dbv.paths import INSTALL_ROOT

LOG_FILE = INSTALL_ROOT / "sniff_log.txt"


def opcode_name(opcode):
    try:
        return ClientOpcode(opcode).name
    except ValueError:
        return "?"


def describe(opcode, payload, with_payload):
    """The log lines for one client packet."""
    shown = (payload.hex(" ") or "(empty)") if with_payload else "[redacted]"
    lines = [f"0x{opcode:02X} ({opcode_name(opcode)}) [{len(payload)} bytes] {shown}"]
    if with_payload and opcode == ClientOpcode.USE_ITEM and len(payload) >= 8:
        x, y, z, item, stack = struct.unpack_from("<HHBHB", payload)
        lines.append(f"  -> USE_ITEM pos=({x}, {y}, {z}) item_id={item} stack={stack}")
    return lines


async def run(bot):
    proxy = bot.state.game_proxy
    with_payload = os.environ.get("DBV_ALLOW_PACKET_CAPTURE") == "1"
    with open(LOG_FILE, "w", encoding="utf-8", buffering=1) as log_file:
        log_file.write("--- Packet diagnostic logger started ---\n")

        def on_client_packet(opcode, reader):
            try:
                lines = describe(opcode, reader.peek_remaining(), with_payload)
            except Exception as exc:
                lines = [f"ERROR: {exc}"]
            bot.log(f"[SNIFF] {lines[0]}")
            log_file.write("".join(line + "\n" for line in lines))

        proxy.register_client_packet_callback(on_client_packet)
        bot.log("[SNIFF] Diagnostyka pakietów włączona; naciśnij teraz swój skrót")
        try:
            for _ in itertools.count():   # until the action is stopped
                await bot.sleep(60.0)
        finally:
            proxy.unregister_client_packet_callback(on_client_packet)
            bot.log("[SNIFF] Diagnostyka pakietów wyłączona")
