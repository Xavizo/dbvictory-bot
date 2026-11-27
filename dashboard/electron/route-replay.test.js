const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const ts=require('typescript');
const source=fs.readFileSync(require.resolve('../lib/routeReplay.ts'),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const exportsForTest={};
new Function('exports',js)(exportsForTest);
const {RouteReplay}=exportsForTest;
const start=1800000000000;
test('lap counters preserve confirmed zero and leave unknown XP unavailable',()=>{
  const s=sample(),run=new RouteReplay(start);
  s.player.level=2;s.player.experience=112;s.player.stats_age=0;
  s.senzu_usage={total:0};s.session_kills=3;
  run.observe(s,true,start);
  assert.deepEqual([run.frames[0].xp,run.frames[0].senzu,run.frames[0].deaths],[112,0,3]);
  s.player.experience=7;s.senzu_usage=undefined;s.session_kills=undefined;
  run.observe(s,true,start+50);
  assert.deepEqual([run.frames[1].xp,run.frames[1].senzu,run.frames[1].deaths],[null,null,null]);
});
test('recorded movement plan survives updates; the next recording starts empty',()=>{
  const state=sample(), run=new RouteReplay(start);
  state.adaptive_cavebot.map_preview={path:[[93,449,10],[93,450,10]],stop:[93,450,10]};
  run.observe(state,true,start);
  run.finish(start+1);
  const saved=run.serialize();
  state.adaptive_cavebot.map_preview.stop[0]=90;
  const next=new RouteReplay(start+2);
  assert.equal(next.frames.length,0);
  assert.deepEqual(JSON.parse(saved).frames[0].plan.stop,[93,450,10]);
  assert.equal(run.serialize(),saved);
});
function sample(cells=[{x:93,y:449,z:10,occupants:0}]){
  return {connected:true,player:{position:[93,449,10],name:'PRIVATE PLAYER'},
    adaptive_cavebot:{name:'Tsuful Podziemia 10',status:'running',loops:1,waypoint:{current:19,last_reached:18},
      combat:{phase:'lure',lure:{goal:3,following:2,engaged:3,nearby:3,waiting:true,stop_reason:'group'}}},
    alerts:{events:[{text:'PRIVATE CHAT'}]},
    passive_map:{center:[93,449,10],captured_at:start/1000,status:'ready',stale:false,cells}};
}
test('route recording keeps local coordinates and group counters with one-based waypoint numbers',()=>{
  const run=new RouteReplay(start);
  const initial=sample([{x:94,y:449,z:10,occupant_types:{monster:1},alert_players:['PRIVATE PLAYER']},
    {x:90,y:450,z:10,occupant_types:{npc:1}},{x:91,y:448,z:10,occupant_types:{monster:2}},
    {x:91,y:448,z:9,occupant_types:{monster:1}},{x:95,y:449,z:10,occupants:2},
    {x:95,y:450,z:10,occupants:1,occupant_types:{pet:1},pet_names:["PRIVATE PET"]}]);
  initial.adaptive_cavebot.blockage={target:[93,450,10],reason:'PRIVATE CHAT'};
  run.observe(initial,true,start);
  initial.player.position[0]=77;
  const frame=run.frames[0];
  assert.deepEqual(frame.position,[93,449,10]);
  assert.deepEqual(frame.monsters,[[91,448,10,2],[94,449,10,1]]);
  assert.equal(frame.unclassified_occupants,2);assert.deepEqual(frame.blocked_target,[93,450,10]);
  assert.equal(frame.waypoint,20);assert.equal(frame.reached,19);
  assert.deepEqual([frame.lure.engaged,frame.lure.goal,frame.lure.reason],[3,3,'group']);
  assert.doesNotMatch(run.serialize(),/PRIVATE/);
});
test('unknown occupancy stays distinct from a fresh empty floor',()=>{
  const run=new RouteReplay(start),s=sample();
  run.observe(s,true,start);assert.deepEqual(run.frames.at(-1).monsters,[]);
  run.observe(s,true,start+2100);assert.equal(run.frames.at(-1).monsters,null);
  s.passive_map.center=[92,449,10];s.passive_map.captured_at=(start+2500)/1000;
  run.observe(s,true,start+3400);assert.equal(run.frames.at(-1).monsters,null);
  run.observe(s,false,start+3410);
  assert.equal(run.frames.at(-1).position,null);assert.equal(run.frames.at(-1).connection,'panel_offline');
});
test('unchanged snapshots are compact, and a completed capture survives subsequent observations',()=>{
  const run=new RouteReplay(start),s=sample();
  assert.equal(run.observe(s,true,start),true);assert.equal(run.observe(s,true,start+10),false);
  assert.equal(run.observe(s,true,start+1000),true);
  run.finish(start+1100);
  const saved=run.serialize();
  assert.equal(run.observe(null,false,start+2000),false);assert.equal(run.serialize(),saved);
  const limit=new RouteReplay(start,10);
  assert.equal(limit.observe(s,true,start),true);assert.equal(limit.full,true);
  assert.equal(limit.frames.length,0);assert.equal(limit.endedAt,start);
});
