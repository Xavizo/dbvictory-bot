"""Verified layouts, selected by the executable digest before process writes."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PlayerLayout:
    stats: int
    conditions: int
    inventory: int
    game_singleton: int
    speed_getter: int
    states_getter: int


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
    player_layout: PlayerLayout | None = None
    observer_dll: str | None = None
    observer_pipe: bytes | None = None


# Retired 2026-11-27: the first client build is no longer accepted (it is not in
# PROFILES). Its layout stays as the reference for synthetic memory fixtures and
# as the key of the map catalogue learned on it.
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
    player_layout=PlayerLayout(0x540, 0x530, 0x46C, 0xB73880, 0xB9DA0, 0xBA1C0),
    observer_dll="dbvobserver_marker_20261117.dll",
    observer_pipe=rb"\\.\pipe\dbvbot_marker_20261117",
)
# Static RTTI, Lua bindings/accessors and constructors reviewed 2026-11-26.
# Windows attachment/gameplay acceptance is separate; see docs/CLIENT_UPDATE.md.
NOVEMBER_26 = ClientProfile(
    "af43383076c0abc7192b407210efc54da6b6cbaf4ff8a2aa18f22a7f7d7486e4",
    UPDATED.dat_sha256,
    0xB79E60, 0xB7A00C, 0xB7A4B0, 0x8C4F4C, 0x8BE72C, 0x8C4F18,
    0x6D6C0, 0x8BE7B0,
    ((0x8BE824, "player"), (0x8BE900, "self"), (0x8C4C3C, "monster"), (0x8C4CFC, "npc")),
    observer=True,
    player_layout=PlayerLayout(0x558, 0x548, 0x484, 0xB79B50, 0xBAEC0, 0xBB310),
    observer_dll="dbvobserver_marker_20261126.dll",
    observer_pipe=rb"\\.\pipe\dbvbot_marker_20261126",
)
PROFILES = {p.sha256: p for p in (UPDATED, NOVEMBER_26)}


CLIENT_UPDATED_MESSAGE = (
    "Klient DBVictory został zaktualizowany, a ta wersja bota jeszcze go nie obsługuje. "
    "Bot nie połączy się z grą, żeby niczego nie zepsuć. Możesz normalnie grać bez bota. "
    "Gdy pojawi się wydanie bota dla nowego klienta, launcher zaproponuje je przy starcie."
)


def client_support(exe):
    """Plain-language answer whether the client executable is a reviewed build."""
    import hashlib
    from pathlib import Path
    path = Path(exe)
    if path.stat().st_size > 64 * 1024 * 1024:
        return {"supported": False, "message": CLIENT_UPDATED_MESSAGE}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest in PROFILES:
        return {"supported": True, "message": "Wersja klienta jest obsługiwana."}
    return {"supported": False, "message": CLIENT_UPDATED_MESSAGE}


def select_profile(digest):
    try:
        return PROFILES[digest]
    except KeyError:
        raise ValueError("Wersja klienta zmieniła się. Wymagana jest ponowna weryfikacja zgodności.") from None
