"""The connection filter used before redirecting the client; never touches real sockets."""
import socket
import struct

from dbv.client_bridge.tcp_reset import connections_to_close


def row(pid, remote_port, state=5):
    port = struct.unpack("<I", struct.pack(">H", remote_port) + b"\0\0")[0]  # network order, low word
    return struct.pack("<6I", state, 0x0100007F, 0x1234, 0x0100007F, port, pid)


def table(*rows, count=None):
    return struct.pack("<I", len(rows) if count is None else count) + b"".join(rows)


def test_only_the_clients_login_and_game_connections_are_chosen():
    data = table(row(10, 7171), row(10, 7172), row(10, 443), row(11, 7172))
    chosen = connections_to_close(data, 10, {7171, 7172})
    assert [socket.ntohs(c.remote_port & 0xFFFF) for c in chosen] == [7171, 7172]
    assert all(c.pid == 10 for c in chosen)


def test_a_table_shorter_than_its_count_is_read_up_to_its_end():
    assert len(connections_to_close(table(row(10, 7172), count=3), 10, {7172})) == 1
    assert connections_to_close(table(), 10, {7172}) == []
