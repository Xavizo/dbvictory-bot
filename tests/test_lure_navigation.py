import random,time
from adaptive_cavebot import varied_detour,step_clear
from test_adaptive_cavebot import setup,cell
from test_automation_safety import state

def test_varied_route_uses_safe_bounded_alternatives_and_diagonals():
    cells=[cell(x,y) for x in range(10,20) for y in range(8,13)]
    reference=[[x,10,7] for x in range(10,19)]
    paths=[varied_detour(cells,reference[0],reference[-1],reference,rng=random.Random(i)) for i in range(20)]
    assert len({tuple(map(tuple,p)) for p in paths})>1
    for p in paths:
        assert p[0]==reference[0] and p[-1]==reference[-1] and len(p)<=len(reference)+2
        assert len(p)==len(set(map(tuple,p)))
        assert all(step_clear(cells,a,b) for a,b in zip(p,p[1:]))
        assert all(abs(v[1]-10)<=2 for v in p)

def test_route_variants_never_cross_unknown_cells_occupants_or_implicit_holes():
    cells=[cell(x,y) for x in range(10,20) for y in range(8,13)]
    for c in cells:
        if c['y']==9:c['navigation']=[dict(kind='hole')]
        if c['y']==11:c.update(occupants=1,occupant_types={'monster':1})
    ref=[[x,10,7] for x in range(10,19)]
    for i in range(10):
        p=varied_detour(cells,ref[0],ref[-1],ref,rng=random.Random(i))
        assert p==ref

def test_variation_preserves_next_waypoint_and_does_not_skip_transition(state,tmp_path):
    cells=[cell(x,y) for x in range(10,24) for y in range(8,13)]
    c,_,_=setup(state,tmp_path,cells=cells,points=[[10,10,7],[18,10,7],[23,10,7]])
    c.status='running';c.edges=[dict(origin=[20,10,7],entry=[21,10,7],exit=[21,10,8])]
    original=c.checkpoints[-1]['waypoint'];assert c.vary_leg(c.map_now())
    assert c.path[c.checkpoints[1]['index']]==[18,10,7]
    assert c.checkpoints[-1]['waypoint']==original
    assert [20,10,7] in c.path and [23,10,7]==c.path[-1]
    path=[v[:] for v in c.path];assert not c.vary_leg(c.map_now());assert c.path==path

def test_clearance_favors_room_over_the_edge_of_a_wall():
    cells=[cell(x,y) for x in range(10,21) for y in range(10,16)]
    ref=[[x,10,7] for x in range(11,20)]
    for seed in range(10):
        route=varied_detour(cells,ref[0],ref[-1],ref,rng=random.Random(seed))
        assert any(p[1]>10 for p in route[1:-1])
        assert len(route)<=len(ref)+2

def test_blockage_distinguishes_unacknowledged_clear_cell_and_occupant(state,tmp_path):
    c,cells,_=setup(state,tmp_path);target=[11,10,7];c.status='running'
    c.blocked[tuple(target)]=time.monotonic()+1;c.describe_blockage(c.map_now(),target)
    assert 'bez potwierdzenia' in c.reason and c.blockage['target']==target
    t=next(t for t in cells if t['x']==11 and t['y']==10)
    t.update(occupants=1,occupant_types={'player':1});c.describe_blockage(c.map_now(),target)
    assert 'zajęte' in c.reason and c.blockage['occupants']=={'player':1}
    c.describe_blockage(c.map_now(),target);assert len(c.logs)==2
