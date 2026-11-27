"""OT ciphers against published vectors and their own inverses."""
import os
import random
import struct

import pytest

from dbv.client_bridge import crypto


def test_xtea_matches_the_published_test_vector():
    key = (0x00010203, 0x04050607, 0x08090A0B, 0x0C0D0E0F)
    block = struct.pack("<2I", 0x41424344, 0x45464748)
    assert struct.unpack("<2I", crypto.xtea_encrypt(block, key)) == (0x497DF3D0, 0x72612CB5)


def test_xtea_round_trip_pads_with_zeros():
    rng = random.Random(7)
    for size in range(0, 40):
        key = tuple(rng.getrandbits(32) for _ in range(4))
        data = os.urandom(size)
        sealed = crypto.xtea_encrypt(data, key)
        assert len(sealed) % 8 == 0 and crypto.xtea_decrypt(sealed, key) == data + b"\0" * (-size % 8)


def test_xtea_rejects_partial_blocks():
    with pytest.raises(ValueError):
        crypto.xtea_decrypt(b"1234567", (1, 2, 3, 4))


def test_otclient_key_is_consistent_and_raw_rsa_round_trips():
    key = crypto.get_default_rsa_key()
    assert key.n.bit_length() == 1024 and key.n % 10**6 == 631413  # the well-known modulus ends ...631413
    message = b"\0" + os.urandom(127)
    assert crypto.rsa_decrypt(crypto.rsa_encrypt(message, key), key) == message


def test_adler32_is_unsigned():
    assert crypto.adler32_checksum(b"Wikipedia") == 0x11E60398
