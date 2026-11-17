const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const ts=require('typescript');
const mod={exports:{}};
new Function('exports',ts.transpileModule(fs.readFileSync(require.resolve('../lib/cavebotModel.ts'),'utf8'),
  {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(mod.exports);
const {visibleWaypointProgress}=mod.exports;
const progress={current:27,last_reached:26,total:45,draft_updated:42,tolerated:1};
const run={draft_id:'route',waypoint:progress};

test('waypoint highlight uses original waypoint identity and survives pause/stop',()=>{
  for(const status of ['running','paused','idle'])assert.deepEqual(visibleWaypointProgress({...run,status},'route',42),progress);
});
test('a different, edited or newly saved draft never shows an old cursor',()=>{
  assert.equal(visibleWaypointProgress(run,'other',42),null);
  assert.equal(visibleWaypointProgress(run,'route',43),null);
  assert.equal(visibleWaypointProgress(run,'route',42,true),null);
  assert.equal(visibleWaypointProgress(null,'route',42),null);
  assert.equal(visibleWaypointProgress({draft_id:'route'},'route',42),null);
});
test('zero-based first waypoint and missing cursor are distinct',()=>{
  assert.equal(visibleWaypointProgress({...run,waypoint:{...progress,current:0}},'route',42).current,0);
  assert.equal(visibleWaypointProgress({...run,waypoint:{...progress,current:45}},'route',42),null);
  assert.equal(visibleWaypointProgress({...run,waypoint:{...progress,current:-1}},'route',42),null);
  assert.equal(visibleWaypointProgress({...run,waypoint:{...progress,current:null}},'route',42).current,null);
});

test('combat/label save keeps the cursor but geometry and unsaved changes do not',()=>{
  const state={...run,waypoint:{...progress,navigation_revision:'same-path'}};
  assert.equal(visibleWaypointProgress(state,'route',99,false,'same-path').current,27);
  assert.equal(visibleWaypointProgress(state,'route',99,false,'different-path'),null);
  assert.equal(visibleWaypointProgress(state,'route',99,true,'same-path'),null);
  assert.equal(visibleWaypointProgress(state,'different-id',99,false,'same-path'),null);
});
