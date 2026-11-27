"""Synthetic keys and frames only; never attach to a game or record traffic."""
import asyncio
import logging
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dbv.client_bridge.client_profiles import NOVEMBER_26, UPDATED
from dbv.client_bridge.crypto import adler32_checksum, xtea_encrypt
from dbv.brain.navigation.passive_map import MapReadError
from dbv.client_bridge.proxy import OTProxy

KEYS = (1, 2, 3, 4)


def frame(payload, keys=KEYS):
    encrypted = xtea_encrypt(struct.pack('<H', len(payload)) + payload, keys)
    return struct.pack('<I', adler32_checksum(encrypted)) + encrypted


def login():
    payload = struct.pack('<BHH', 10, 10, 372) + bytes(128)
    return struct.pack('<I', adler32_checksum(payload)) + payload


def source():
    from dbv.client_bridge.client_protocol import SIGNATURES
    base, ptr, pid = 0x400000, 0x20000, 17
    link = base + 0xB79B50 + 0x24
    memory = {link: struct.pack('<II', ptr, 0x30000),
              ptr: struct.pack('<I', base + 0x8BE5FC),
              ptr+0x18: struct.pack('<4I', *KEYS), ptr+0x3E: b'\x01'}
    memory.update({base+rva: code for rva, code in SIGNATURES.items()})
    def read(address, length):
        value = memory.get(address)
        if value is None or len(value) != length:
            raise MapReadError('unmapped synthetic address')
        return value
    return SimpleNamespace(base=base, pid=pid, profile=NOVEMBER_26, read=read), memory, ptr, link


def test_verified_protocol_key_is_read_without_executing_client_code():
    from dbv.client_bridge.client_protocol import read_protocol_key
    client, *_ = source()
    assert read_protocol_key(client, 17) == KEYS


@pytest.mark.parametrize('expected_pid',[17,18])
def test_live_adapter_closes_the_read_handle_on_success_or_rejection(monkeypatch, expected_pid):
    from dbv.client_bridge.client_protocol import read_live_protocol_key
    from dbv.client_bridge import passive_map_win32
    client, *_ = source()
    client.close = Mock()
    monkeypatch.setattr(passive_map_win32,'ReadOnlyClient',lambda:client)
    if expected_pid == 17:
        assert read_live_protocol_key(expected_pid) == KEYS
    else:
        with pytest.raises(MapReadError): read_live_protocol_key(expected_pid)
    client.close.assert_called_once()


@pytest.mark.parametrize('problem', ['pid', 'profile', 'vtable', 'disabled', 'empty', 'signature', 'torn_key', 'torn_link'])
def test_unverified_or_unstable_protocol_key_is_rejected(problem):
    from dbv.client_bridge.client_protocol import read_protocol_key, SIGNATURES
    client, memory, ptr, link = source()
    if problem == 'pid': client.pid = 18
    if problem == 'profile': client.profile = UPDATED
    if problem == 'vtable': memory[ptr] = struct.pack('<I', client.base+0x8CA758)
    if problem == 'disabled': memory[ptr+0x3E] = b'\0'
    if problem == 'empty': memory[ptr+0x18] = bytes(16)
    if problem == 'signature': memory[client.base+next(iter(SIGNATURES))] = b'bad'
    original = client.read
    calls = 0
    def torn(address, length):
        nonlocal calls
        value = original(address, length)
        if address == (ptr+0x18 if problem == 'torn_key' else link) and problem.startswith('torn_'):
            calls += 1
            if calls > 1: return bytes(length)
        return value
    client.read = torn
    with pytest.raises(MapReadError): read_protocol_key(client, 17)


def test_new_client_needs_login_and_two_valid_frames_before_key_activation():
    read = Mock(return_value=KEYS)
    proxy = OTProxy('synthetic', 1, 1, client_key_reader=read)
    assert proxy._confirm_client_key(frame(b'\x1d')) == []
    read.assert_not_called()
    assert proxy._process_login_packet(login()) == login()
    first, second = frame(b'\x0a'+bytes(8)), frame(b'\x1d')
    assert proxy._confirm_client_key(first) == []
    assert proxy.xtea_keys is None
    assert proxy._confirm_client_key(second) == [first, second]
    assert proxy.xtea_keys == KEYS


@pytest.mark.parametrize('problem', ['wrong_key', 'checksum', 'length', 'opcode', 'read_error', 'login_error'])
def test_invalid_key_or_frame_never_activates_session(problem):
    read = Mock(return_value=(4,3,2,1) if problem == 'wrong_key' else KEYS)
    if problem == 'read_error': read.side_effect = MapReadError('unavailable')
    proxy = OTProxy('synthetic', 1, 1, client_key_reader=read)
    proxy._process_login_packet(login())
    raw = frame(b'\x1d')
    if problem == 'checksum': raw = b'bad!'+raw[4:]
    if problem == 'length': raw = frame(b'')
    if problem == 'opcode': raw = frame(b'\xff')
    if problem == 'login_error': raw = frame(b'\x14')
    for _ in range(20): assert proxy._confirm_client_key(raw) == []
    assert proxy.xtea_keys is None and not proxy.logged_in
    assert read.call_count <= 8


@pytest.mark.parametrize('raw', [b'', b'\0'*137, frame(b'\x1d'), login()[:-1]])
def test_only_the_known_login_envelope_allows_reading_the_client_key(raw):
    read = Mock(return_value=KEYS)
    proxy = OTProxy('synthetic',1,1,client_key_reader=read)
    assert proxy._process_login_packet(raw) == raw
    assert proxy._confirm_client_key(frame(b'\x1d')) == []
    read.assert_not_called()


def test_protocol_read_errors_never_expose_exception_contents(caplog):
    read = Mock(side_effect=ValueError('synthetic-private-material'))
    proxy = OTProxy('synthetic',1,1,client_key_reader=read)
    proxy._process_login_packet(login())
    with caplog.at_level(logging.DEBUG):
        for _ in range(8): proxy._confirm_client_key(frame(b'\x1d'))
    assert 'synthetic-private-material' not in caplog.text
    assert 'relay continues without automation' in caplog.text


def test_changed_key_discards_previous_confirmation_and_frame():
    newer = (5,6,7,8)
    read = Mock(side_effect=[KEYS,newer,newer])
    proxy = OTProxy('synthetic', 1, 1, client_key_reader=read)
    proxy._process_login_packet(login())
    assert proxy._confirm_client_key(frame(b'\x1d')) == []
    first, second = frame(b'\x1d',newer), frame(b'\x1e',newer)
    assert proxy._confirm_client_key(first) == []
    assert proxy._confirm_client_key(second) == [first,second]
    assert proxy.xtea_keys == newer
    proxy._reset_client_key()
    assert proxy._confirm_client_key(second) == []


def test_real_server_relay_forwards_then_activates_and_observes_both_frames():
    async def run():
        proxy = OTProxy('synthetic',1,1,client_key_reader=lambda:KEYS)
        proxy._process_login_packet(login())
        first, second = frame(b'\x0a'+bytes(8)), frame(b'\x1d')
        incoming = iter([first,second,None])
        async def read_packet(_): return next(incoming)
        proxy._read_packet = read_packet
        proxy.server_reader = object()
        events = []
        async def drain(): pass
        proxy.client_writer = SimpleNamespace(write=lambda _:events.append('forward'),drain=drain)
        proxy.on_login_success = lambda _:events.append('login')
        proxy.on_encrypted_server_data = lambda raw,keys,generation:events.append(('observe',raw,keys))
        await proxy._relay_server_to_client()
        assert events[:3] == ['forward','forward','login']
        assert events[3:] == [('observe',first,KEYS),('observe',second,KEYS)]
    asyncio.run(run())


def test_connection_teardown_and_replacement_discard_pending_native_keys(monkeypatch):
    async def run():
        proxy = OTProxy('synthetic',1,1,client_key_reader=lambda:KEYS)
        async def drained(): pass
        def writer(): return SimpleNamespace(close=Mock(),wait_closed=drained)
        async def upstream(*_): return object(),writer()
        monkeypatch.setattr(asyncio,'open_connection',upstream)
        async def relay():
            assert not proxy._client_login_seen and not proxy._key_frames
            assert proxy.xtea_keys is None
            proxy._process_login_packet(login())
            proxy._confirm_client_key(frame(b'\x1d'))
        proxy._run_relay_loop = relay
        for _ in range(2):
            await proxy._handle_client_connection(object(),writer())
            assert proxy.xtea_keys is None and not proxy.logged_in
            assert not proxy._key_frames and proxy._candidate_keys is None
    asyncio.run(run())


def test_failed_legacy_rsa_never_logs_packet_contents(caplog, monkeypatch):
    from dbv.client_bridge import proxy as module
    proxy = OTProxy('synthetic',1,1)
    proxy._proxy_rsa_key = proxy.default_rsa_key
    monkeypatch.setattr(module,'rsa_decrypt',lambda *_:b'\xff'*128)
    raw = login()
    with caplog.at_level(logging.DEBUG): assert proxy._process_login_packet(raw) == raw
    assert 'Packet hex' not in caplog.text
    assert raw[:32].hex() not in caplog.text


def test_legacy_default_rsa_handshake_is_preserved():
    from dbv.client_bridge.crypto import rsa_encrypt
    proxy = OTProxy('synthetic',1,1)
    plain = b'\0'+struct.pack('<4I',*KEYS)+bytes(111)
    payload = struct.pack('<BHH',10,10,372)+rsa_encrypt(plain,proxy.default_rsa_key)
    raw = struct.pack('<I',adler32_checksum(payload))+payload
    assert proxy._process_login_packet(raw) == raw
    assert proxy.xtea_keys == KEYS
