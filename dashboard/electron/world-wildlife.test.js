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

test('world position expires and cannot come from an offline preview',()=>{
  const map={status:'ready',center:[23,42,7],captured_at:20};
  const get=(extra={},now=20500,connected=true)=>model.atlasPlayer({connected,passive_map:{...map,...extra}},now);
  assert.deepEqual(get(),map.center);
  for(const result of [get({preview:true}),get({stale:true}),get({},23000),get({},20500,false)])assert.equal(result,null);
});
