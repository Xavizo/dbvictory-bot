"""How a cavebot recording becomes an actions map.

Recordings here are written as journeys: a ``Trip`` starts on a tile and
records what the player does (steps, clicks, uses) the way the recorder
would. Most tests compare the whole route, written one node per string:

    "walk 100,199,7"      walk_to
    "walk! 100,199,7"     walk_to that must end exactly on the tile
    "use 1696 137,564,6"  use_item <item id> <target>
    "use_with 2120 ..."   use_item_ex <item id> <target>
"""
from __future__ import annotations

import random

import pytest

from dbv.brain.cavebot.cavebot import (
    DEFAULT_GROUND_ID, NOT_ON_MAP, STEP,
    _is_map_click_walk, _simplify_path, actions_map_to_text, build_actions_map,
)

GRASS = 486


class Trip:
    """Builds raw recorder waypoints while tracking where the player stands."""

    def __init__(self, x, y, z):
        self.here = [x, y, z]
        self.waypoints: list[dict] = []

    def _record(self, waypoint, t=0.0):
        waypoint.setdefault("t", t)
        self.waypoints.append(waypoint)
        return self

    def step(self, direction, times=1):
        dx, dy = STEP[direction]
        for _ in range(times):
            start = list(self.here)
            self.here = [start[0] + dx, start[1] + dy, start[2]]
            self._record({"type": "walk", "direction": direction, "pos": list(self.here), "player_pos": start})
        return self

    def autowalk(self, x, y, z):
        start, self.here = list(self.here), [x, y, z]
        return self._record({"type": "walk", "direction": "autowalk", "pos": [x, y, z], "player_pos": start})

    def teleport(self, x, y, z):
        """Move without a waypoint, as stairs or the server do."""
        self.here = [x, y, z]
        return self

    def floor_event(self, x, y, z, t=0.0):
        direction = "up" if z < self.here[2] else "down"
        self.here = [x, y, z]
        return self._record({"type": "floor_change", "direction": direction, "pos": [x, y, z], "z": z}, t)

    def use(self, x, y, z, item, **extra):
        return self._record({"type": "use_item", "x": x, "y": y, "z": z, "item_id": item,
                             "stack_pos": 0, "index": 0, "pos": list(self.here), **extra})

    def click(self, x, y, item=GRASS, z=None):
        """Click a map tile and walk there; the walk stays on the player's floor."""
        self.use(x, y, self.here[2] if z is None else z, item)
        self.here = [x, y, self.here[2]]
        return self

    def use_with(self, item, to, source=(NOT_ON_MAP, 0, 0), **extra):
        return self._record({"type": "use_item_ex", "from_x": source[0], "from_y": source[1],
                             "from_z": source[2], "item_id": item, "stack_pos": 0,
                             "to_x": to[0], "to_y": to[1], "to_z": to[2], "to_stack_pos": 0,
                             "pos": list(self.here), **extra})

    def door_changes(self, x, y, z):
        return self._record({"type": "tile_transform_item", "x": x, "y": y, "z": z})

    def sample(self):
        return self._record({"type": "position", "pos": list(self.here)})

    def raw(self, waypoint):
        return self._record(dict(waypoint))

    @property
    def recording(self):
        return {"waypoints": self.waypoints}

    def nodes(self):
        return build_actions_map(self.recording)

    def route(self):
        return [_spell(node) for node in self.nodes()]


def _spell(node):
    where = ",".join(map(str, node["target"]))
    mark = "!" if node.get("exact") else ""
    kind = {"walk_to": "walk", "use_item": "use", "use_item_ex": "use_with"}[node["type"]]
    item = "" if kind == "walk" else f" {node['item_id']}"
    return f"{kind}{mark}{item} {where}"


# ── nothing to build ────────────────────────────────────────────────

@pytest.mark.parametrize("recording", [{}, {"waypoints": []}])
def test_a_recording_without_waypoints_has_an_empty_map(recording):
    assert build_actions_map(recording) == []


def test_position_samples_and_unknown_waypoints_add_nothing():
    trip = Trip(100, 200, 7).sample().raw({"type": "something_new", "pos": [1, 2, 7]}).sample()
    assert trip.route() == []


def test_uses_from_a_hotkey_or_container_are_not_part_of_the_route():
    assert Trip(100, 200, 7).use(NOT_ON_MAP, 0, 0, 266).route() == []


# ── walking with the keyboard ───────────────────────────────────────

@pytest.mark.parametrize("direction", sorted(STEP))
def test_a_keyboard_step_targets_the_tile_it_ends_on(direction):
    dx, dy = STEP[direction]
    assert Trip(100, 200, 7).step(direction).route() == [f"walk {100 + dx},{200 + dy},7"]


def test_a_keyboard_walk_keeps_a_tile_about_every_three_steps_and_the_last_one():
    assert Trip(100, 200, 7).step("north", 5).route() == [
        "walk 100,199,7", "walk 100,196,7", "walk 100,195,7"]
    long_walk = Trip(100, 200, 7).step("north", 20).route()
    assert long_walk == [f"walk 100,{y},7" for y in (199, 196, 193, 190, 187, 184, 181, 180)]


def test_turning_does_not_split_a_walk():
    assert Trip(100, 200, 7).step("north", 2).step("east", 2).route() == [
        "walk 100,199,7", "walk 102,198,7"]


def test_a_step_recorded_twice_is_one_tile():
    trip = Trip(100, 200, 7).step("north").teleport(100, 200, 7).step("north")
    assert trip.route() == ["walk 100,199,7"]


def test_position_samples_do_not_split_a_walk():
    trip = Trip(105, 200, 7).step("west").sample().step("west").sample().sample().step("west")
    assert trip.route() == ["walk 104,200,7", "walk 102,200,7"]


def test_autowalks_target_their_destination_and_are_thinned_like_steps():
    trip = Trip(99, 200, 7)
    for x in range(100, 106):
        trip.autowalk(x, 200, 7)
    assert trip.route() == ["walk 100,200,7", "walk 103,200,7", "walk 105,200,7"]


def test_autowalks_and_steps_form_one_walk():
    trip = Trip(99, 200, 7).autowalk(100, 200, 7).step("east").autowalk(105, 200, 7)
    assert trip.route() == ["walk 100,200,7", "walk 105,200,7"]


def test_walk_nodes_carry_the_ground_item_and_the_waypoints_they_came_from():
    assert Trip(100, 200, 7).step("north").step("north").nodes() == [
        {"type": "walk_to", "target": [100, 199, 7], "item_id": DEFAULT_GROUND_ID,
         "stack_pos": 1, "wp_range": [0, 1]},
        {"type": "walk_to", "target": [100, 198, 7], "item_id": DEFAULT_GROUND_ID,
         "stack_pos": 1, "wp_range": [0, 1]},
    ]


# ── stairs and floor changes ────────────────────────────────────────

def test_walking_down_stairs_keeps_the_stair_tile_exact_and_the_first_tile_below():
    trip = (Trip(132, 564, 6).step("west", 4)      # the last step lands on the stairs at x=128
            .teleport(127, 564, 7).step("west", 3))
    assert trip.route() == [
        "walk 131,564,6", "walk! 128,564,6", "walk 126,564,7", "walk 124,564,7"]


def test_a_floor_change_event_marks_the_tile_before_it_exact():
    trip = Trip(130, 564, 6).step("west", 2).floor_event(127, 564, 7).step("west", 2)
    assert trip.route() == [
        "walk 129,564,6", "walk! 128,564,6", "walk 126,564,7", "walk 125,564,7"]


def test_a_recording_that_ends_on_stairs_still_reaches_them_exactly():
    assert Trip(130, 564, 6).step("west", 2).floor_event(127, 564, 7).route() == [
        "walk 129,564,6", "walk! 128,564,6"]


def test_every_floor_boundary_survives_thinning():
    trip = (Trip(100, 203, 6).step("north", 2)
            .teleport(100, 201, 7).step("north", 2)
            .teleport(100, 199, 6).step("north"))
    assert trip.route() == [
        "walk 100,202,6", "walk! 100,201,6", "walk 100,200,7", "walk! 100,199,7", "walk 100,198,6"]


def test_a_round_trip_through_stairs_marks_both_approaches():
    trip = (Trip(112, 564, 7).step("south", 3)     # the stairs are at y=567
            .teleport(112, 568, 6).step("south").step("east")
            .autowalk(113, 566, 6).autowalk(113, 563, 7))
    assert trip.route() == [
        "walk 112,565,7", "walk! 112,567,7", "walk 112,569,6", "walk! 113,566,6", "walk 113,563,7"]


def test_a_stale_floor_change_from_before_a_use_is_not_its_effect():
    trip = Trip(110, 200, 7).use(120, 200, 7, 4445).floor_event(120, 200, 6, t=-10)
    assert trip.route() == ["walk 110,200,7", "walk! 120,200,7"]


def test_a_floor_change_that_keeps_the_floor_is_not_an_effect():
    trip = Trip(110, 200, 7).use(120, 200, 7, 4445).floor_event(120, 200, 7)
    assert trip.route() == ["walk 110,200,7", "walk 120,200,7"]


# ── clicking the map to walk ────────────────────────────────────────

def test_a_map_click_walks_from_where_the_player_stood_to_the_clicked_tile():
    trip = Trip(100, 200, 7).click(120, 200)
    assert trip.route() == ["walk 100,200,7", "walk 120,200,7"]
    assert {node["item_id"] for node in trip.nodes()} == {GRASS}
    assert {node["stack_pos"] for node in trip.nodes()} == {0}


def test_a_click_on_a_tile_shown_from_another_floor_walks_on_the_players_floor():
    assert Trip(100, 200, 7).click(120, 200, z=6).route() == ["walk 100,200,7", "walk 120,200,7"]


def test_clicks_then_steps_follow_the_path_in_order():
    trip = Trip(100, 200, 7).click(120, 200).click(130, 200).step("north", 2)
    assert trip.route() == [
        "walk 100,200,7", "walk 120,200,7", "walk 130,200,7", "walk 130,199,7", "walk 130,198,7"]


def test_a_click_cut_short_by_a_step_ends_where_that_step_started():
    trip = Trip(100, 200, 7).use(110, 200, 7, GRASS).teleport(104, 200, 7).step("east")
    assert trip.route() == ["walk 100,200,7", "walk 104,200,7", "walk 105,200,7"]


def test_keyboard_walks_borrow_the_ground_of_a_nearby_click():
    after = Trip(100, 200, 7).click(120, 200, item=GRASS).step("north").nodes()
    assert after[-1] == {"type": "walk_to", "target": [120, 199, 7], "item_id": GRASS,
                         "stack_pos": 1, "wp_range": [1, 1]}
    before = Trip(100, 200, 7).step("east").click(110, 200, item=777).nodes()
    assert before[0]["item_id"] == 777


def test_keyboard_walks_long_after_a_click_reuse_the_first_clicked_ground():
    trip = Trip(100, 200, 7).click(120, 200, item=GRASS)
    for _ in range(5):
        trip.sample()
    assert trip.step("north").nodes()[-1]["item_id"] == GRASS


def test_only_the_first_clicked_ground_is_remembered():
    trip = Trip(100, 200, 7).click(120, 200, item=GRASS).step("north").click(130, 199, item=777)
    for _ in range(5):
        trip.sample()
    assert trip.step("north").nodes()[-1]["item_id"] == GRASS


def test_ground_is_walked_even_when_clicked_twice():
    trip = Trip(132, 582, 6).click(129, 581).teleport(131, 582, 6).click(129, 581)
    assert trip.route() == ["walk 132,582,6", "walk 129,581,6"]


# ── is this use a click to walk? ────────────────────────────────────

@pytest.mark.parametrize("waypoint,walks", [
    (Trip(100, 200, 7).use(110, 200, 7, GRASS), True),         # far away
    (Trip(100, 200, 7).use(100, 202, 7, GRASS), True),         # two steps
    (Trip(100, 200, 7).use(100, 201, 7, 1696), False),         # next to the player
    (Trip(100, 200, 7).use(100, 200, 7, 1968), False),         # under the player
    (Trip(100, 200, 7).use(101, 200, 6, 1968), False),         # next to them, one floor up
    (Trip(540, 971, 7).use(540, 972, 7, 4561), True),          # known ground, even adjacent
    (Trip(540, 971, 7).use(540, 972, 7, 4560), False),         # the stairs beside that ground
    (Trip(100, 200, 7).use(NOT_ON_MAP, 0, 0, 266), False),     # hotkey
    (Trip(100, 200, 7).use(0, 0, 0, 266), False),              # no position
    (Trip(100, 200, 7).use(110, 200, 7, 1771, index=1), False),  # opened from a container index
    (Trip(100, 200, 7).step("north"), False),                  # not a use at all
])
def test_which_uses_are_clicks_to_walk(waypoint, walks):
    assert _is_map_click_walk(waypoint.waypoints[-1]) is walks


# ── using things ────────────────────────────────────────────────────

def test_a_use_next_to_the_player_keeps_every_detail():
    assert Trip(10, 21, 7).use(10, 20, 7, 1234, stack_pos=3, index=2).nodes() == [{
        "type": "use_item", "target": [10, 20, 7], "x": 10, "y": 20, "z": 7, "item_id": 1234,
        "stack_pos": 3, "index": 2, "label": "Use item 1234", "player_pos": [10, 21, 7],
        "wp_range": [0, 0]}]


def test_a_use_with_keeps_both_ends():
    trip = Trip(100, 201, 7).use_with(2120, (100, 200, 7), stack_pos=5, to_stack_pos=2)
    assert trip.nodes() == [{
        "type": "use_item_ex", "target": [100, 200, 7], "from_x": NOT_ON_MAP, "from_y": 0, "from_z": 0,
        "item_id": 2120, "stack_pos": 5, "to_x": 100, "to_y": 200, "to_z": 7, "to_stack_pos": 2,
        "label": "Use item 2120", "player_pos": [100, 201, 7], "wp_range": [0, 0]}]


def test_a_door_on_the_way_is_approached_exactly():
    trip = (Trip(134, 564, 6).step("east", 2)
            .use(137, 564, 6, 1696).step("west", 2))
    assert trip.route() == [
        "walk 135,564,6", "walk! 136,564,6", "use 1696 137,564,6", "walk 135,564,6", "walk 134,564,6"]


def test_a_use_interrupting_a_walk_splits_it():
    trip = Trip(100, 205, 7).step("north", 2).use(100, 202, 7, 1696).step("north", 2)
    assert trip.route() == [
        "walk 100,204,7", "walk! 100,203,7", "use 1696 100,202,7", "walk 100,202,7", "walk 100,201,7"]


def test_the_walk_before_a_use_with_is_exact():
    trip = Trip(99, 200, 7).autowalk(100, 200, 7).use_with(2120, (100, 200, 8))
    assert trip.route() == ["walk! 100,200,7", "use_with 2120 100,200,8"]


def test_uses_themselves_are_never_marked_exact():
    trip = Trip(100, 201, 7).use(100, 200, 7, 1696).use(100, 200, 6, 1968)
    assert trip.route() == ["use 1696 100,200,7", "use 1968 100,200,6"]


@pytest.mark.parametrize("trip", [
    Trip(140, 575, 7).use(137, 579, 7, 1968).floor_event(138, 578, 6),   # ladder
    Trip(105, 200, 7).use(100, 200, 7, 9999).floor_event(103, 200, 6),   # any item that moves you
])
def test_a_far_use_that_changes_the_floor_is_kept_as_a_use(trip):
    use = trip.waypoints[0]
    assert trip.route() == [f"use {use['item_id']} {use['x']},{use['y']},{use['z']}"]


def test_a_far_door_is_a_use_only_when_its_tile_changes():
    opened = Trip(137, 580, 6).use(130, 581, 6, 1771).door_changes(130, 581, 6)
    assert opened.route() == ["use 1771 130,581,6"]
    elsewhere = Trip(137, 580, 6).use(130, 581, 6, 1771).door_changes(131, 581, 6)
    assert elsewhere.route() == ["walk 137,580,6", "walk 130,581,6"]
    other_floor = Trip(137, 580, 6).use(130, 581, 6, 1771).door_changes(130, 581, 5)
    assert other_floor.route() == ["walk 137,580,6", "walk 130,581,6"]
    unchanged = Trip(137, 580, 6).use(130, 581, 6, 1771)
    assert unchanged.route() == ["walk 137,580,6", "walk 130,581,6"]


def test_a_far_door_opened_from_a_container_index_is_a_use():
    assert Trip(137, 580, 6).use(130, 581, 6, 1771, index=1).route() == ["use 1771 130,581,6"]


def test_clicks_leading_to_a_ladder_walk_up_to_it_exactly():
    trip = (Trip(100, 200, 7).click(120, 200).click(130, 200)
            .use(137, 200, 7, 5000).floor_event(137, 200, 6))
    assert trip.route() == ["walk 100,200,7", "walk 120,200,7", "walk! 130,200,7", "use 5000 137,200,7"]


def test_known_ground_is_never_an_interaction():
    trip = Trip(535, 970, 7).use(540, 972, 7, 4561).floor_event(543, 971, 6)
    assert trip.route() == ["walk 535,970,7", "walk! 540,972,7"]


@pytest.mark.parametrize("item", [4561, 4445])
def test_stairs_taken_by_keyboard_belong_to_the_steps_not_an_earlier_click(item):
    trip = (Trip(535, 970, 7).use(540, 972, 7, item)
            .teleport(536, 971, 7).step("east", 6)
            .floor_event(543, 971, 6).step("north"))
    assert trip.route() == [
        "walk 535,970,7", "walk 536,971,7", "walk 537,971,7", "walk 540,971,7",
        "walk! 542,971,7", "walk 543,970,6"]


def test_a_use_with_owns_the_floor_change_after_it():
    trip = (Trip(110, 200, 7).use(120, 200, 7, 4445).teleport(119, 200, 7)
            .use_with(2120, (120, 200, 7)).floor_event(120, 200, 6))
    assert trip.route() == ["walk 110,200,7", "walk! 120,200,7", "use_with 2120 120,200,7"]


def test_a_ladder_a_door_and_a_walk_in_one_recording():
    trip = (Trip(140, 575, 7).use(137, 579, 7, 1968).floor_event(137, 579, 6)
            .teleport(135, 580, 6).use(132, 582, 6, 1771).door_changes(132, 582, 6)
            .teleport(133, 581, 6).use(132, 582, 6, 1771).door_changes(132, 582, 6)
            .teleport(132, 582, 6).click(129, 581))
    assert trip.route() == ["use 1968 137,579,7", "use 1771 132,582,6", "walk 132,582,6", "walk 129,581,6"]


# ── repeated nodes ──────────────────────────────────────────────────

def test_repeats_of_one_use_fold_into_the_first_and_span_all_their_waypoints():
    trip = Trip(99, 200, 7).use(100, 200, 7, 1054).use(100, 200, 7, 1054).use(100, 200, 7, 1058)
    nodes = trip.nodes()
    assert [_spell(node) for node in nodes] == ["use 1054 100,200,7"]
    assert nodes[0]["wp_range"] == [0, 2]


def test_repeats_fold_only_when_neighbours_of_the_same_kind():
    trip = Trip(99, 200, 7).autowalk(100, 200, 7).use(100, 201, 7, 1696).autowalk(100, 200, 7)
    assert trip.route() == ["walk! 100,200,7", "use 1696 100,201,7", "walk 100,200,7"]
    side_by_side = Trip(100, 201, 7).autowalk(100, 200, 7).use(100, 200, 7, 1696)
    assert side_by_side.route() == ["walk! 100,200,7", "use 1696 100,200,7"]


def test_ten_identical_autowalks_are_one_node():
    trip = Trip(99, 200, 7)
    for _ in range(10):
        trip.autowalk(100, 200, 7)
    assert trip.route() == ["walk 100,200,7"]
    assert trip.nodes()[0]["wp_range"] == [0, 9]


def test_a_long_walk_there_and_back_keeps_its_one_door():
    trip = Trip(100, 200, 7).step("east", 50).use(151, 200, 7, 1696).step("west", 50)
    route = trip.route()
    assert route.count("use 1696 151,200,7") == 1
    assert len(route) < 50
    assert route[route.index("use 1696 151,200,7") - 1] == "walk! 150,200,7"


# ── thinning a path ─────────────────────────────────────────────────

def _line(n, z=7):
    return [(100 + i, 200, z, GRASS, 1) for i in range(n)]


@pytest.mark.parametrize("points", [[], _line(1), _line(2)])
def test_short_paths_are_kept_whole(points):
    assert _simplify_path(points) == points


@pytest.mark.parametrize("gap,kept", [(3, [0, 3, 6, 9]), (5, [0, 5, 9]), (20, [0, 9])])
def test_a_straight_path_keeps_a_point_every_gap(gap, kept):
    points = _line(10)
    assert _simplify_path(points, max_gap=gap) == [points[i] for i in kept]


def test_points_further_apart_than_the_gap_are_all_kept():
    points = [(100 + 5 * i, 200, 7, GRASS, 1) for i in range(4)]
    assert _simplify_path(points) == points


def test_random_paths_keep_their_ends_floor_boundaries_and_spacing():
    rng = random.Random(7)
    for _ in range(300):
        x, y, z, points = 100, 200, 7, []
        for _ in range(rng.randint(2, 40)):
            x += rng.randint(-2, 2)
            y += rng.randint(-2, 2)
            if rng.random() < 0.1:
                z += rng.choice((-1, 1))
            points.append((x, y, z, GRASS, len(points)))   # unique, so kept points can be found
        gap = rng.randint(1, 6)
        kept = _simplify_path(points, max_gap=gap)
        assert kept[0] == points[0] and kept[-1] == points[-1]
        assert all(a in kept and b in kept for a, b in zip(points, points[1:]) if a[2] != b[2])
        # Every dropped point lies within the gap of the last point kept before it.
        assert kept == sorted(kept, key=points.index)
        for point in points:
            if point in kept:
                last_kept = point
            else:
                assert abs(point[0] - last_kept[0]) + abs(point[1] - last_kept[1]) < gap


# ── the text preview ────────────────────────────────────────────────

def test_the_preview_numbers_nodes_and_names_their_waypoints():
    trip = (Trip(134, 564, 6).step("east", 2)
            .use(137, 564, 6, 1696, label="Door").step("west", 2)
            .use(134, 564, 5, 1968))
    assert actions_map_to_text(trip.nodes()).splitlines() == [
        "1. walk_to (135,564,6) [wp 1-2]",
        "2. walk_to (136,564,6) [exact] [wp 1-2]",
        "3. use_item Door (137,564,6) [wp 3-3]",
        "4. walk_to (135,564,6) [wp 4-5]",
        "5. walk_to (134,564,6) [exact] [wp 4-5]",
        "6. use_item Use item 1968 (134,564,5) *floor change* [wp 6-6]",
    ]


def test_the_preview_describes_step_lists_and_skips_unknown_nodes():
    nodes = [{"type": "walk_steps", "target": [1, 2, 7], "label": "ramp",
              "steps": [{"direction": "north"}, {"direction": "east"}]},
             {"type": "teleport", "target": [5, 5, 7], "label": "?"}]
    assert actions_map_to_text(nodes) == "1. walk_steps (1,2,7) *ramp* [north, east]"
    assert actions_map_to_text([]) == ""
