// Build-specific addresses. The host pins the executable SHA-256 before loading.
// See docs/CLIENT_UPDATE.md for static evidence and outstanding Windows acceptance.
#pragma once
#if defined(DBV_CLIENT_20261126)
static constexpr uintptr_t MAP_BLOCKS = 0xB79E60, MAP_CENTER = 0xB7A00C;
static constexpr uintptr_t VT_PLAYER = 0x8BE824, VT_SELF = 0x8BE900;
static constexpr uintptr_t VT_MONSTER = 0x8C4C3C, VT_NPC = 0x8C4CFC, VT_TILE = 0x8C4F4C;
static constexpr uintptr_t GAME = 0xB79B50;
static constexpr uintptr_t SHOW_SQUARE = 0xBA920, HIDE_SQUARE = 0xBA960;
static constexpr uintptr_t PEEK_IAT = 0x7AE3A4, PEEK_SITE_1 = 0x405455, PEEK_SITE_2 = 0x405480;
static constexpr uint32_t SQUARE_VISIBLE = 0x15D, SQUARE_COLOR = 0x13C;
static const char* OBSERVER_PIPE = "\\\\.\\pipe\\dbvbot_marker_20261126";
#else
static constexpr uintptr_t MAP_BLOCKS = 0xB73B90, MAP_CENTER = 0xB73D3C;
static constexpr uintptr_t VT_PLAYER = 0x8BA8D4, VT_SELF = 0x8BA9B0;
static constexpr uintptr_t VT_MONSTER = 0x8C0CB4, VT_NPC = 0x8C0D74, VT_TILE = 0x8C0FC4;
static constexpr uintptr_t GAME = 0xB73880;
static constexpr uintptr_t SHOW_SQUARE = 0xB9800, HIDE_SQUARE = 0xB9840;
static constexpr uintptr_t PEEK_IAT = 0x7A939C, PEEK_SITE_1 = 0x400535, PEEK_SITE_2 = 0x400560;
static constexpr uint32_t SQUARE_VISIBLE = 0x145, SQUARE_COLOR = 0x124;
static const char* OBSERVER_PIPE = "\\\\.\\pipe\\dbvbot_marker_20261117";
#endif
