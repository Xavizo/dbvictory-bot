import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import brand_exe  # noqa: E402


def test_the_bots_icon_becomes_one_icon_group_with_every_size():
    images = brand_exe.read_ico((ROOT / "launcher" / "desktop.ico").read_bytes())
    assert {(w or 256) for w, *_ in images} >= {16, 32, 48, 256}
    group = brand_exe.group_icon(images)
    reserved, kind, count = struct.unpack_from("<HHH", group)
    assert (reserved, kind, count) == (0, 1, len(images)) and len(group) == 6 + 14 * len(images)
    entries = [struct.unpack_from("<BBBBHHIH", group, 6 + 14 * i) for i in range(count)]
    assert [e[7] for e in entries] == list(range(1, count + 1))  # RT_ICON ids
    assert [e[6] for e in entries] == [len(image[5]) for image in images]


def test_a_broken_icon_is_refused():
    for data in (b"\0\0\2\0\1\0" + b"\0" * 16, b"\0\0\1\0\1\0" + struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 32, 999, 22)):
        try:
            brand_exe.read_ico(data)
        except ValueError:
            continue
        raise AssertionError("accepted a broken icon")
