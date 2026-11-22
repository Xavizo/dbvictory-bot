const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const model = {};
new Function('exports',ts.transpileModule(fs.readFileSync(require.resolve('../lib/atlasWildlife.ts'),'utf8'),
  {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(model);

test('world locates each species at its first sighting and keeps different floors distinct',()=>{
  const mark={point:[10,12,6],observer_cell:[16,8,6],name:'Wolf',count:2};
  const index=model.sectorHabitats([mark,{...mark,name:'Bat',count:1},{...mark,point:[9,14,6]},
    {...mark,point:[10,12,7]},{...mark,name:'Rat',count:0}],8);
  assert.deepEqual(index.names,['Bat','Wolf']);
  assert.deepEqual(index.habitats,[{position:[8,8,6],species:[['Wolf',4],['Bat',1]]},
    {position:[8,8,7],species:[['Wolf',2]]}]);
});

test('atlas centres on stored coordinates after the marker expires',()=>{
  const state={connected:true,player:{position:[30,40,8]},passive_map:{center:[29,40,8]}};
  assert.deepEqual(model.atlasLocation(state,[31,40,8]),[31,40,8]);
  assert.deepEqual(model.atlasLocation(state,null),[30,40,8]);
  assert.deepEqual(model.atlasLocation({...state,player:{position:[0,0,0]}},null),[29,40,8]);
  assert.equal(model.atlasLocation({...state,passive_map:{...state.passive_map,preview:true}},null),null);
});

test('ground selection distinguishes floors, coordinates and overlapping grounds',()=>{
  const choices=new Map([['above',{cells:[[24,32,6]]}],['draft:route',{cells:[[24,32,7],[32,32,7]]}],['room',{cells:[[24,32,7]]}]]);
  assert.equal(model.localGround(choices,[31,39,7],8),'room');
  assert.equal(model.localGround(choices,[31,39,7],8,'route'),'draft:route');
  assert.equal(model.localGround(choices,[32,39,7],8),'draft:route');
  assert.equal(model.localGround(choices,[31,39,8],8),'');
  assert.equal(model.localGround(choices,null,8),'');
});

test('world position expires and cannot come from an offline preview',()=>{
  const map={status:'ready',center:[23,42,7],captured_at:20};
  const get=(extra={},now=20500,connected=true)=>model.atlasPlayer({connected,passive_map:{...map,...extra}},now);
  assert.deepEqual(get(),map.center);
  for(const result of [get({preview:true}),get({stale:true}),get({},23000),get({},20500,false)])assert.equal(result,null);
});
