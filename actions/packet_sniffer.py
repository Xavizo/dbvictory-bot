"""Log packet metadata; raw payloads require explicit diagnostic opt-in."""
import sys, os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dbv.client_bridge.protocol import ClientOpcode

SNIFF_LOG = Path(__file__).parent.parent / "sniff_log.txt"


async def run(bot):
    state = sys.modules["__main__"].state
    proxy = state.game_proxy

    def sniffer(opcode, reader):
        try:
            try:
                name = ClientOpcode(opcode).name
            except ValueError:
                name = "?"

            raw = reader.peek_remaining()
            # Payloads may contain chat and other private game data.
            include_payload = os.environ.get("DBV_ALLOW_PACKET_CAPTURE") == "1"
            hex_dump = (raw.hex(" ") if raw else "(empty)") if include_payload else "[redacted]"

            line = f"0x{opcode:02X} ({name}) [{len(raw)} bytes] {hex_dump}\n"
            bot.log(f"[SNIFF] {line.strip()}")
            with open(SNIFF_LOG, "a") as f:
                f.write(line)

            # Also parse known packet types for readability
            if include_payload and opcode == ClientOpcode.USE_ITEM and len(raw) >= 8:
                import struct
                x = struct.unpack_from('<H', raw, 0)[0]
                y = struct.unpack_from('<H', raw, 2)[0]
                z = raw[4]
                item_id = struct.unpack_from('<H', raw, 5)[0]
                stack = raw[7]
                detail = f"  -> USE_ITEM pos=({x}, {y}, {z}) item_id={item_id} stack={stack}\n"
                with open(SNIFF_LOG, "a") as f:
                    f.write(detail)

        except Exception as e:
            with open(SNIFF_LOG, "a") as f:
                f.write(f"ERROR: {e}\n")

    proxy.register_client_packet_callback(sniffer)
    with open(SNIFF_LOG, "w") as f:
        f.write("--- Packet diagnostic logger started ---\n")
    bot.log("[SNIFF] Packet diagnostic logger installed — press your hotkey now")

    try:
        while True:
            await bot.sleep(60)
    except BaseException:
        proxy.unregister_client_packet_callback(sniffer)
        bot.log("[SNIFF] Removed")
