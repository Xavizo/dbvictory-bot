"""Verified layouts, selected by the executable digest before process writes."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ClientProfile:
    sha256: str
    dat_sha256: str
    map_blocks: int
    map_center: int
    item_types: int
    tile_vtable: int
    item_vtable: int
    type_vtable: int
    true_getter: int
    u16_attribute_vtable: int
    creature_vtables: tuple
    observer: bool = False


LEGACY = ClientProfile(
    "828ea9e11c6890f92cc14956733bac3aa7b80f57bfd8bb8e225568780c6e4b40",
    "26e6342c67378183dd422d3a044a7ef159e27ebfe2af1594fae8e0a35f200c91",
    0xB6E470, 0xB6E61C, 0xB6ED00, 0x8BCFC0, 0x8B6890, 0x8BCF8C,
    0x6C940, 0x8B6900,
    ((0x8B6974, "player"), (0x8B6A50, "self"), (0x8BCCB0, "monster"), (0x8BCD70, "npc")),
)
UPDATED = ClientProfile(
    "8c15e00a00c9f68c05291fbe0bb6669c362c780fe901f2580299db999a54f5e8",
    "cc6d91160218e7565ab1247d19ae483485dedaf6a18e24a05978ef31badb3669",
    0xB73B90, 0xB73D3C, 0xB741E0, 0x8C0FC4, 0x8BA7F0, 0x8C0F90,
    0x6CA40, 0x8BA860,
    ((0x8BA8D4, "player"), (0x8BA9B0, "self"), (0x8C0CB4, "monster"), (0x8C0D74, "npc")),
    observer=True,
)
PROFILES = {p.sha256: p for p in (LEGACY, UPDATED)}


def select_profile(digest):
    try:
        return PROFILES[digest]
    except KeyError:
        raise ValueError("Wersja klienta zmieniła się. Wymagana jest ponowna weryfikacja zgodności.") from None
