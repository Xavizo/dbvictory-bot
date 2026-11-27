"""Redirecting the client changes only copies of the server address."""
import pytest

from dbv.client_bridge.client_redirect import find_text, redirect_server_address


class FakeMemory:
    def __init__(self, regions, unreadable=(), read_only=()):
        self.bytes = {base: bytearray(data) for base, data in regions.items()}
        self.unreadable, self.read_only = set(unreadable), set(read_only)

    def _region(self, address):
        for base, data in self.bytes.items():
            if base <= address < base + len(data):
                return base, data
        raise OSError("unmapped")

    def regions(self):
        return [(base, len(data)) for base, data in self.bytes.items()]

    def read(self, address, size):
        base, data = self._region(address)
        if base in self.unreadable:
            raise OSError("gone")
        return bytes(data[address - base:address - base + size])

    def write(self, address, data):
        base, region = self._region(address)
        if base in self.read_only:
            return  # silently ignored, as a protected page would be
        region[address - base:address - base + len(data)] = data


HOST = "87.98.220.215"


def test_every_copy_is_replaced_and_padded_with_zeros():
    memory = FakeMemory({0x1000: b"xx87.98.220.215\0yy87.98.220.215", 0x9000: b"87.98.220.2150"})
    assert redirect_server_address(memory, HOST) == 3
    assert memory.bytes[0x1000] == bytearray(b"xx127.0.0.1\0\0\0\0\0yy127.0.0.1\0\0\0\0")
    assert memory.bytes[0x9000] == bytearray(b"127.0.0.1\0\0\0\0" + b"0")


def test_unreadable_regions_are_skipped_and_failed_writes_not_counted():
    memory = FakeMemory({0x1000: b"87.98.220.215", 0x2000: b"87.98.220.215", 0x3000: b"87.98.220.215"},
                        unreadable={0x2000}, read_only={0x3000})
    assert list(find_text(memory, HOST.encode())) == [0x1000, 0x3000]
    assert redirect_server_address(memory, HOST) == 1


def test_already_redirected_client_changes_nothing():
    memory = FakeMemory({0x1000: b"127.0.0.1\0\0\0\0"})
    assert redirect_server_address(memory, HOST) == 0


def test_proxy_address_must_fit():
    with pytest.raises(ValueError):
        redirect_server_address(FakeMemory({}), "1.2.3.4", b"127.0.0.1")
