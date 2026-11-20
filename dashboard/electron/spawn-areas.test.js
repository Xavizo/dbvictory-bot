const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const ts=require('typescript');
const code=ts.transpileModule(fs.readFileSync(require.resolve('../lib/spawnAreas.ts'),'utf8'),{
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}
}).outputText;
const model={};
new Function('exports',code)(model);
const sight=(x,y,z,count=1,name='Wolf')=>({point:[x,y,z],name,count});

test('repeat sightings group by species and floor without adding populations or chaining down a hallway',()=>{
  const rows=[sight(15,8,7,80),sight(16,8,7,40),sight(13,9,7),sight(16,8,6),sight(16,8,7,2,'Fox')];
  for(let x=18;x<=34;x+=2)rows.push(sight(x,8,7));
  const alone=model.spawnAreas(rows,[]);
  assert.ok(alone.every(a=>a.minimum===null && a.radius<=4));
  assert.ok(alone.some(a=>a.position[2]===6) && alone.some(a=>a.name==='Fox'));
  const pack={name:'Wolf',points:[[15,8,7],[16,8,7],[13,9,7]],observer_cell:[8,8,7]};
  const areas=model.spawnAreas(rows,[pack,pack]);
  const ordered=groups=>groups.slice().sort((a,b)=>[a.name,...a.position].join(",").localeCompare([b.name,...b.position].join(",")));
  assert.deepEqual(ordered(areas),ordered(model.spawnAreas([...rows].reverse(),[pack])));
  assert.deepEqual(areas.filter(g=>g.minimum!==null).map(g=>g.minimum),[3]);
  assert.equal(areas.reduce((n,g)=>n+g.sightings,0),rows.reduce((n,r)=>n+r.count,0));
  assert.ok(areas.length<rows.length);
});

test('all species share a regional marker, with separate observed lower bounds, without summing packs',()=>{
  const rows=[sight(13,9,7,11,'Fox'),sight(14,9,7,14),sight(15,9,7,42)];
  const frames=[
    {name:'Wolf',points:[[14,9,7],[15,9,7]],observer_cell:[8,8,7]},
    {name:'Fox',points:[[13,9,7],[13,9,7],[13,9,7]],observer_cell:[8,8,7]},
  ];
  const [area,...others]=model.spawnAreas(rows,frames,true);
  assert.equal(others.length,0);
  assert.equal(area.minimum,3);
  assert.deepEqual(area.species.get('Fox'),{sightings:11,minimum:3});
  assert.deepEqual(area.species.get('Wolf'),{sightings:56,minimum:2});
});

test('visible now counts only current wild occupants in the selected visible area and expires on stale data',()=>{
  const point=[16,8,7],cells=new Set(['16,8,7']);
  const cell={x:17,y:8,z:7,occupants:5,occupant_types:{monster:2,pet:1,npc:1,unknown:1}};
  const map={center:point,status:'ready',captured_at:100,cells:[cell,{...cell,x:10},{...cell,z:8}]};
  const game={connected:true,player:{position:point},passive_map:map};
  assert.deepEqual(model.visibleMonsters(game,cells,8,101000),{count:2,unknown:1,marks:[{position:[17,8,7],count:2}],floor:7});
  assert.equal(model.visibleMonsters(game,cells,8,103000),null);
  assert.equal(model.visibleMonsters(game,new Set(['32,8,7']),8,100001),null);
  assert.equal(model.visibleMonsters({...game,connected:false},cells,8,100001),null);
  assert.equal(model.visibleMonsters({...game,player:{position:[20,8,7]}},cells,8,100001),null);
  map.cells=[{...cell,occupants:1,occupant_types:{pet:1}}];
  assert.equal(model.visibleMonsters(game,cells,8,100001).count,0);
});
