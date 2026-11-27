"""The relay learns keys, rewrites the character list and forwards frames unchanged."""
import asyncio
import struct

from dbv.client_bridge import proxy as relay
from dbv.client_bridge.constants import SERVER_IP_BYTES, SERVER_IP_STR
from dbv.client_bridge.crypto import adler32_checksum, get_default_rsa_key, rsa_encrypt, xtea_decrypt, xtea_encrypt
from dbv.client_bridge.proxy import OTProxy, find_session_keys, point_character_list_at_proxy

KEYS = (11, 22, 33, 44)


def sealed(packet, keys=KEYS):
    body = xtea_encrypt(struct.pack("<H", len(packet)) + packet, keys)
    return struct.pack("<I", adler32_checksum(body)) + body


def login_packet(keys=KEYS):
    block = rsa_encrypt(b"\0" + struct.pack("<4I", *keys) + bytes(111), get_default_rsa_key())
    payload = b"\x01" + bytes(17) + block
    return struct.pack("<I", adler32_checksum(payload)) + payload


def test_session_keys_come_from_the_rsa_block():
    assert find_session_keys(login_packet(), [get_default_rsa_key()]) == KEYS
    assert find_session_keys(b"\0" * 40, [get_default_rsa_key()]) is None


def test_character_list_points_every_world_at_localhost():
    packet = b"\x64\x01" + b"World\0" + SERVER_IP_BYTES + b"\x1c\x1b" + SERVER_IP_STR + b"!"
    out = point_character_list_at_proxy(sealed(packet), KEYS)
    assert struct.unpack_from("<I", out)[0] == adler32_checksum(out[4:])
    plain = xtea_decrypt(out[4:], KEYS)
    size = struct.unpack_from("<H", plain)[0]
    expected = (b"\x64\x01" + b"World\0" + b"\x7f\0\0\x01" + b"\x1c\x1b"
                + b"127.0.0.1".ljust(len(SERVER_IP_STR), b"\0") + b"!")
    assert plain[2:2 + size] == expected


def test_reply_that_is_not_whole_blocks_is_left_alone():
    assert point_character_list_at_proxy(b"\1\2\3", KEYS) is None


def test_the_proxy_key_is_only_generated_when_the_otclient_key_fails(monkeypatch):
    made = []
    monkeypatch.setattr(relay, "generate_proxy_rsa_keypair", lambda: made.append(1) or get_default_rsa_key())
    proxy = OTProxy("synthetic", 1, 1)
    assert proxy._process_login_packet(login_packet()) == login_packet()
    assert proxy.xtea_keys == KEYS and not made


def test_game_relay_forwards_both_ways_and_shows_client_packets_to_the_bot():
    async def scenario():
        seen, from_client = [], asyncio.Queue()

        async def game_server(reader, writer):
            frame = await reader.readexactly(struct.unpack("<H", await reader.readexactly(2))[0])
            await from_client.put(frame)
            reply = sealed(b"\x1d")
            writer.write(struct.pack("<H", len(reply)) + reply)
            await writer.drain()
            await asyncio.sleep(0.2)
            writer.close()

        upstream = await asyncio.start_server(game_server, "127.0.0.1", 0)
        port = upstream.sockets[0].getsockname()[1]
        proxy = OTProxy("127.0.0.1", port, 0)
        proxy.register_client_packet_callback(lambda opcode, reader: seen.append((opcode, reader.read_u32())))
        listener = await asyncio.start_server(proxy._handle_client_connection, "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
        await asyncio.sleep(0.05)
        proxy.xtea_keys, proxy.logged_in = KEYS, True
        attack = sealed(b"\xa1" + struct.pack("<I", 0x40000001))
        writer.write(struct.pack("<H", len(attack)) + attack)
        await writer.drain()
        assert await asyncio.wait_for(from_client.get(), 2) == attack   # unchanged
        size = struct.unpack("<H", await asyncio.wait_for(reader.readexactly(2), 2))[0]
        assert await reader.readexactly(size) == sealed(b"\x1d")
        assert seen == [(0xA1, 0x40000001)]
        writer.close()
        for server in (listener, upstream):
            server.close()
    asyncio.run(scenario())
