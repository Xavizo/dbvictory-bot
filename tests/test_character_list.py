import pytest

from launcher.character_list import character_list_visible, labels


def frame_at(scale, heading_at, button_at, width=680, height=550):
    frame = bytearray(width * height)
    for pattern, position in zip(labels(scale), (heading_at, button_at)):
        if position is None:
            continue
        x, y = position
        for j, row in enumerate(pattern):
            pos = (y+j)*width+x
            frame[pos:pos+len(row)] = row
    return bytes(frame), width, height


@pytest.mark.parametrize("scale, dpi", [(1, 96), (1, 144), (1.25, 120), (1.5, 144)])
def test_character_list_can_move_or_scale(scale, dpi):
    screen = frame_at(scale, (90, 31), (90+round(10*scale), 31+round(218*scale)))
    assert character_list_visible(*screen, dpi)


@pytest.mark.parametrize("heading,button", [
    (None, None), ((90, 31), None), (None, (100, 249)),
    ((90, 31), (350, 249)), ((90, 31), (100, 49)),
])
def test_incomplete_or_unrelated_labels_stay_disabled(heading, button):
    assert not character_list_visible(*frame_at(1, heading, button))
