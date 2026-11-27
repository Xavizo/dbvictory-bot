"""Ciphers of the Open Tibia protocol: raw RSA for the login block, XTEA for
everything after it, and Adler-32 as the packet checksum.

XTEA here is the OT variant: 32 rounds, little-endian 32-bit words, data
padded with zero bytes to whole 8-byte blocks.
"""
import struct
import zlib

from Crypto.PublicKey import RSA

# The public OTClient key, published with its factors in the OTClient source.
# Servers that kept it (DBVictory does) accept logins encrypted with it.
_OTCLIENT_P = int(
    "14299623962416399520070177382898895550795403345466153217470516082934737582776038882967213386"
    "204600674145392845853859217990626450972452084065728686565928113")
_OTCLIENT_Q = int(
    "76309791959704047218912018477920021255354012927791239372074475745966927885136471792353355293"
    "07251350570728407373705564708871762033017096809910315212884101")
DEFAULT_RSA_E = 65537
DEFAULT_RSA_N = _OTCLIENT_P * _OTCLIENT_Q
DEFAULT_RSA_D = pow(DEFAULT_RSA_E, -1, (_OTCLIENT_P - 1) * (_OTCLIENT_Q - 1))

_MASK = 0xFFFFFFFF
_DELTA = 0x9E3779B9
_ROUNDS = 32
# The round keys' running sums, the same for every block.
_SUMS = [(_DELTA * i) & _MASK for i in range(_ROUNDS + 1)]


def generate_proxy_rsa_keypair():
    """A fresh 1024-bit key; the patched client encrypts its login block to it."""
    return RSA.generate(1024)


def get_default_rsa_key():
    return RSA.construct((DEFAULT_RSA_N, DEFAULT_RSA_E, DEFAULT_RSA_D))


def _raw_rsa(block: bytes, exponent: int, modulus: int) -> bytes:
    # OT uses textbook RSA on one modulus-sized block, without PKCS#1 padding.
    size = (modulus.bit_length() + 7) // 8
    return pow(int.from_bytes(block, "big"), exponent, modulus).to_bytes(size, "big")


def rsa_decrypt(data: bytes, private_key) -> bytes:
    return _raw_rsa(data, private_key.d, private_key.n)


def rsa_encrypt(data: bytes, public_key) -> bytes:
    return _raw_rsa(data, public_key.e, public_key.n)


def _blocks(data: bytes):
    return struct.iter_unpack("<2I", data)


def xtea_encrypt(data: bytes, key) -> bytes:
    """Encrypt, padding with zeros to whole 8-byte blocks."""
    k = tuple(key)
    data = bytes(data) + b"\0" * (-len(data) % 8)
    out = bytearray()
    for v0, v1 in _blocks(data):
        for r in range(_ROUNDS):
            s, s_next = _SUMS[r], _SUMS[r + 1]
            v0 = (v0 + ((((v1 << 4) ^ (v1 >> 5)) + v1) ^ (s + k[s & 3]))) & _MASK
            v1 = (v1 + ((((v0 << 4) ^ (v0 >> 5)) + v0) ^ (s_next + k[(s_next >> 11) & 3]))) & _MASK
        out += struct.pack("<2I", v0, v1)
    return bytes(out)


def xtea_decrypt(data: bytes, key) -> bytes:
    """Decrypt whole 8-byte blocks; any other length is an error."""
    if len(data) % 8:
        raise ValueError(f"XTEA data must be whole 8-byte blocks, got {len(data)} bytes")
    k = tuple(key)
    out = bytearray()
    for v0, v1 in _blocks(data):
        for r in range(_ROUNDS, 0, -1):
            s, s_prev = _SUMS[r], _SUMS[r - 1]
            v1 = (v1 - ((((v0 << 4) ^ (v0 >> 5)) + v0) ^ (s + k[(s >> 11) & 3]))) & _MASK
            v0 = (v0 - ((((v1 << 4) ^ (v1 >> 5)) + v1) ^ (s_prev + k[s_prev & 3]))) & _MASK
        out += struct.pack("<2I", v0, v1)
    return bytes(out)


def adler32_checksum(data: bytes) -> int:
    return zlib.adler32(data) & _MASK
