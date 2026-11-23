const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
// Use the project's TS compiler; works on every supported Node version.
const model = {exports:{}};
new Function('exports', ts.transpileModule(fs.readFileSync(require.resolve('../lib/atlasModel.ts'),'utf8'),
  {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(model.exports);
const {atlasCenter,visibleAtlasCells,routeLines,simulationStep,atlasViewport,panCenter,routeSegments,stepFloor,atlasWheel,segmentLane,moveAnchor}=model.exports;
const cell=(x,y,z,kind='clear')=>({x,y,z,kind,items:[],navigation:[]});
test('replace only the selected visit to the Gotrunks stairs and keep the other floor crossings',()=>{
  const {editableFloorLinks,nearbyTransitions,replaceFloorLink}=model.exports;
  const old={origin:[1039,1300,4],entry:[1038,1301,4],exit:[1037,1301,3],id:'south',action:'step'};
  const north={...old,entry:[1038,1300,4],exit:[1037,1300,3],id:'north'};
  const down={...old,id:'down',origin:old.exit,entry:[1038,1301,3],exit:old.origin};
  const points=[[1041,1298,4],old.origin,old.exit,old.origin,old.exit,[1039,1299,3]];
  const links=[{from_index:1,id:'south'},{from_index:2,id:'down'},{from_index:3,id:'south'}];
  const legs=points.slice(1).map((_,i)=>({from_index:i,to_index:i+1,start:i,end:i+1}));
  const plan={legs,path:points,links,transitions:[old,down,old]};
  assert.deepEqual(editableFloorLinks(plan),links);
  assert.deepEqual(nearbyTransitions([old,north,down,{...north,id:'far',entry:[1000,1299,4]}],old),[north]);
  const edit=replaceFloorLink(points,links,editableFloorLinks(plan)[2],north);
  assert.deepEqual(edit.points,[...points.slice(0,4),north.exit,points[5]]);
  assert.deepEqual(edit.links,[links[0],links[1],{from_index:3,id:'north'}]);
  assert.deepEqual(edit.sourceIndices,[0,1,2,3,4,5]);
  assert.throws(()=>replaceFloorLink(points,links,links[2],down));
  assert.deepEqual(editableFloorLinks({...plan,legs:[...legs,{from_index:5,to_index:4,start:5,end:6}],
    path:[...points,old.origin],transitions:[...plan.transitions,{...down,origin:points[5]}]}),[...links,undefined]);
});
test('replacement preserves shared endpoints, labels, and the seam of a closed sketch',()=>{
  const a=[10,10,4],b=[10,10,3],c=[10,10,2],from=[11,10,4],to=[11,10,3];
  const links=[{from_index:0,id:'old'},{from_index:1,id:'next'}];
  const result=model.exports.replaceFloorLink([a,b,c,a],links,links[0],{id:'other',origin:from,entry:[12,10,4],exit:to});
  assert.deepEqual(result.points,[a,from,to,b,c,a]);
  assert.deepEqual(result.links,[{from_index:1,id:'other'},{from_index:3,id:'next'}]);
  assert.deepEqual(result.sourceIndices,[0,null,null,1,2,3]);
});
test('explicit transition survives insertion and undo but moving an endpoint revokes it',()=>{
  const {remapLinks,rememberEdit}=model.exports;
  const points=[[10,10,7],[20,20,6],[21,20,6]],links=[{from_index:0,id:'stairs'}];
  assert.deepEqual(remapLinks(links,points,[[9,10,7],...points]),[{from_index:1,id:'stairs'}]);
  assert.deepEqual(remapLinks(links,points,[points[0],[19,20,6],points[2]]),[]);
  assert.deepEqual(remapLinks(links,points,[points[0],points[2]]),[]);
  const history=rememberEdit([],points,[10,10,7],undefined,links);links[0].id='changed';
  assert.deepEqual(history[0].links,[{from_index:0,id:'stairs'}]);
});
test('old draft transitions can be explicitly confirmed in place without losing later floors',()=>{
  const points=[[10,10,7],[20,20,6],[30,30,5],[31,30,5]];
  const up={id:'up',origin:points[0],entry:[10,9,7],exit:points[1],action:'step'};
  let result=model.exports.selectTransition(points,[],up);
  assert.deepEqual(result.points,points);assert.deepEqual(result.links,[{from_index:0,id:'up'}]);
  result=model.exports.selectTransition(result.points,result.links,{...up,id:'up2',origin:points[1],exit:points[2]});
  assert.deepEqual(result.points,points);assert.deepEqual(result.links,[{from_index:0,id:'up'},{from_index:1,id:'up2'}]);
});
test('waypoint labels follow insertion, deletion, dragging and undo',()=>{
  const {remapLabels,rememberEdit}=model.exports;
  const before=[[10,10,7],[12,10,7],[14,10,7]],labels=['Start','Door','End'];
  assert.deepEqual(remapLabels(before,[[10,10,7],[11,10,7],[12,10,7],[14,10,7]],labels),['Start','','Door','End']);
  assert.deepEqual(remapLabels(before,[[10,10,7],[14,10,7]],labels),['Start','End']);
  assert.deepEqual(remapLabels(before,[[10,10,7],[12,11,7],[14,10,7]],labels),labels);
  const history=rememberEdit([],before,[10,10,7],labels);labels[1]='changed';
  assert.equal(history[0].labels[1],'Door');
});
test('following moves XY and Z; browsing keeps manual floor',()=>{
  assert.deepEqual(atlasCenter(true,[10,10,7],[15,11,6]),[15,11,6]);
  assert.deepEqual(atlasCenter(false,[10,10,7],[15,11,6]),[10,10,7]);
});
test('fresh map replaces historical door state without erasing surrounding atlas',()=>{
  const old={...cell(10,10,7,'blocked'),navigation:[{kind:'door',door_state_hint:'closed'}]};
  const fresh={...cell(10,10,7),navigation:[{kind:'door',door_state_hint:'open'}]};
  const map={status:'ready',stale:false,annotation_scope:'profile',cells:[fresh,cell(11,10,7,'unknown')]};
  const merged=visibleAtlasCells([old,cell(11,10,7)],map,'profile',[10,10,7],12);
  assert.equal(merged[0].navigation[0].door_state_hint,'open');assert.equal(merged[0].live,true);
  assert.equal(merged[1].kind,'clear');assert.equal(merged[1].live,false);
  for(const rejected of [{...map,stale:true},{...map,annotation_scope:'different'},{...map,status:'waiting'}])
    assert.equal(visibleAtlasCells([old],rejected,'profile',[10,10,7],12)[0].kind,'blocked');
});
test('pet labels follow only live occupants and disappear on returning to historical terrain',()=>{
  const saved=[cell(10,10,7),cell(11,10,7)];
  const map={status:'ready',stale:false,annotation_scope:'test',cells:[]};
  for(const index of [0,1]) {
    map.cells=[{...saved[index],occupants:1,occupant_types:{pet:1},pet_names:['Bills']}];
    const merged=visibleAtlasCells(saved,map,'test',[10,10,7],12);
    assert.deepEqual(merged[index].pet_names,['Bills']);
    assert.equal(merged[1-index].pet_names,undefined);
  }
  assert.ok(visibleAtlasCells(saved,{...map,stale:true},'test',[10,10,7],12).every(c=>!c.pet_names));
});
test('display distinguishes unidentified occupants and the owners of two pets without changing combat counts',()=>{
  const {displayOccupants,petDescription}=component('../lib/mapVisuals.ts');
  const tile={occupant_types:{monster:2,pet:2},unidentified_monsters:1,
    pet_names:['Bills','Bills'],pet_owners:['Trainer','Another Player']};
  assert.deepEqual(displayOccupants(tile),{monster:1,pet:2,unknown:1});
  assert.equal(tile.occupant_types.monster,2);
  assert.equal(petDescription(tile),'Bills (właściciel: Trainer), Bills (właściciel: Another Player)');
});
test('floor changes and teleports do not draw fictitious walk lines',()=>{
  assert.deepEqual(routeLines([[10,10,7],[11,10,7],[50,50,7],[50,50,6],[12,10,7]],7),
    [[[10,10,7],[11,10,7]],[[50,50,7]],[[12,10,7]]]);
});
test('simulation reports transition before landing and stops at path end',()=>{
  const path=[[10,10,7],[20,20,6]],edge={origin:path[0],exit:path[1],action:'use_with'};
  assert.equal(simulationStep(path,0,[edge]).transition.action,'use_with');
  assert.deepEqual(simulationStep(path,1,[edge]),{current:[20,20,6],next:undefined,transition:undefined});
});
test('rectangular map fills its viewport with square tiles and bounded fetch',()=>{
  for(const [width,height] of [[1200,500],[400,800],[1900,250]]) {
    const grid=atlasViewport(width,height,12);
    assert.ok(Math.abs(width/grid.columns-height/grid.rows)<.001);
    assert.ok(grid.queryRadius<=32 && grid.columns<=65);
  }
  assert.deepEqual(panCenter([100,200,7],100,-50,10),[90,205,7]);
  assert.deepEqual(panCenter([1,65534,6],100,-100,10),[0,65534,6]);
});
test('return to the same floor creates a new ordered segment',()=>{
  const path=[[1,1,7],[2,1,7],[2,2,6],[3,2,6],[2,1,7],[1,1,7]];
  const segments=routeSegments(path);
  assert.deepEqual(segments.map(s=>[s.floor,s.start,s.end]),[[7,0,1],[6,2,3],[7,4,5]]);
  assert.deepEqual(segments[0].points,path.slice(0,2));
  assert.deepEqual(segments[2].points,path.slice(4));
});
test('Ctrl wheel changes floors, Shift wheel zooms, and plain scroll does neither',()=>{
  assert.deepEqual(atlasWheel(-120,true,false),{kind:'floor',delta:-1});
  assert.deepEqual(atlasWheel(120,false,true),{kind:'zoom',delta:1});
  assert.equal(atlasWheel(120,true,true).kind,'floor');
  assert.equal(atlasWheel(120,false,false).kind,'none');
  assert.equal(atlasWheel(0,true,false).kind,'none');
  assert.equal(stepFloor(0,-1),0);assert.equal(stepFloor(15,1),15);
  assert.equal(stepFloor(7,-1),6);assert.equal(stepFloor(7,1),8);
  assert.equal(atlasViewport(250,500,2).queryRadius,5);
});
test('overlapping return segments get separate lanes; editing a point preserves floor order',()=>{
  const points=[[10,10,7],[10,10,6],[12,10,7]];
  const seg=routeSegments(points);
  assert.notEqual(segmentLane(seg,0),segmentLane(seg,2));
  assert.deepEqual(moveAnchor(points,1,[11,10,5]),[[10,10,7],[11,10,5],[12,10,7]]);
  assert.deepEqual(points[1],[10,10,6]);
});
function component(file,imports={}) {
  if (file.endsWith('/MapTools.tsx') || file.endsWith('/ProfilePicker.tsx')) imports['./ActionMenu']=component('../app/ActionMenu.tsx');
  if (file.endsWith('/CavebotRunBar.tsx')) imports['./LureStatus']=component('../app/LureStatus.tsx');
  if (file.endsWith('/AtlasPanel.tsx')) imports['./DraftTags'] = component('../app/DraftTags.tsx');
  const mod={exports:{}};
  new Function('exports','require',ts.transpileModule(fs.readFileSync(require.resolve(file),'utf8'),
    {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText)
    (mod.exports,spec=>imports[spec] ?? (spec === "./SessionStats" ? component("../app/SessionStats.tsx") : spec === "./ActivationCheckbox" ? component("../app/ActivationCheckbox.tsx") : spec === "./AlertBell" ? component("../app/AlertBell.tsx", {"@/lib/api":{}}) : spec === "./ProfilePicker" ? component("../app/ProfilePicker.tsx", {"@/lib/auth":{}}) : require(spec)));
  return mod.exports;
}
const spellPicker=component('../app/SpellPicker.tsx',{'@/lib/spellCatalogue':component('../lib/spellCatalogue.ts')});

test('saved sketch offers individual colored editable tags without loading the map',()=>{
  const tags=[{text:'12M XP/h',color:'mint'},{text:'Lvl 390',color:'blue'}];
  const Tags=component('../app/DraftTags.tsx').default;
  const html=require('react-dom/server').renderToStaticMarkup(require('react').createElement(Tags,{tags,onSave:()=>{}}));
  assert.match(html,/data-color="mint".*Edytuj etykietę 12M XP\/h/);
  assert.match(html,/data-color="blue".*Edytuj etykietę Lvl 390/);
  assert.match(html,/\+ Etykieta/);
});

test('global Atlas is available outside Cavebot and remains unloaded until opened',()=>{
  let mounts=0;
  const chrome=component('../app/DashboardChrome.tsx',{
    'next/dynamic':{default:()=>()=>{mounts++;return null;}},'./CavebotRunBar':{default:()=>null},
  });
  const render=require('react-dom/server').renderToStaticMarkup,el=require('react').createElement;
  for(const view of ['automation','cavebot','diagnostics']) {
    const html=render(el(chrome.default,{view,state:null,mcpConnected:false,busy:false,error:'',children:'content'}));
    assert.match(html,/aria-label="Otwórz atlas i mapę"/);
    assert.doesNotMatch(html,/atlas-canvas|global-atlas-dialog/);
  }
  assert.equal(mounts,0);
});

test('spell conditions expose comparisons, absolute HP/Ki and explain priority without casting',()=>{
  const {default:Conditions,SpellDiagnostics}=component('../app/SpellConditions.tsx');
  const render=(C,props)=>require('react-dom/server').renderToStaticMarkup(require('react').createElement(C,props));
  const html=render(Conditions,{value:{words:'Shunkan',aim:'self',trigger:'distance',range:1,count:2,ki:13500,ki_unit:'value',hp:80000,hp_unit:'value'},onChange:()=>{}});
  for(const op of ['&gt;','&gt;=','=','&lt;=','&lt;'])assert.ok(html.includes(`value="${op}"`));
  assert.match(html,/value="13500"/);assert.match(html,/value="80000"/);
  assert.equal((html.match(/value="value" selected=""/g)||[]).length,2);
  const output=render(SpellDiagnostics,{passive:true,rows:[{words:'Shunkan',selected:true,matched_count:2,reason:'Pierwszy gotowy — priorytet'}]});
  assert.match(output,/2 mob./);assert.match(output,/bez rzucania czarów/);assert.match(output,/Pierwszy gotowy/);
  const wave=render(SpellDiagnostics,{rows:[{words:'Shinretsuha',matched_count:1,reason:'Wave: czekam na potwierdzenie obrotu',wave_note:'Wave: tylko 1 pole wprost; pełny obszar Shinretsuha niepotwierdzony'}]});
  assert.match(wave,/potwierdzenie obrotu/);assert.match(wave,/pełny obszar Shinretsuha niepotwierdzony/);
});

test('whole-route view contains every floor and both visits on the current floor',()=>{
  const overview=component('../app/AtlasRouteOverview.tsx',{'@/lib/atlasModel':model.exports});
  const panel=component('../app/AtlasPanel.tsx',{
    '@/lib/atlasModel':model.exports,'@/lib/atlasLoading':component('../lib/atlasLoading.ts'),'@/lib/auth':{authenticatedFetch:()=>{throw Error('no requests during render');}},
    '@/lib/cavebotModel':component('../lib/cavebotModel.ts'),
    '@/lib/mapVisuals':{tileStyles:{},navigationStyles:{}},'./PassiveMapPanel':{NavigationDetails:()=>null},
    './TransitionLearningPanel':{transitionActions:{}},'./AtlasRouteOverview':overview,'./MapTools':component('../app/MapTools.tsx'),
  });
  const state={connected:true,passive_map:{center:[100,100,7],cells:[]},cavebot:{recording:{active:true,observed_path:
    [[100,100,7],[101,100,7],[101,100,6],[102,100,6],[101,100,7],[100,100,7]].map(pos=>({pos}))},recordings:[]}};
  const render=(Component,props)=>require('react-dom/server').renderToStaticMarkup(require('react').createElement(Component,props));
  const html=render(panel.default,{state,active:true,onRefresh:()=>{}});
  assert.match(html, /<button[^>]*aria-pressed="true"[^>]*aria-label="Śledź postać"/);
  assert.doesNotMatch(html, /class="atlas-follow"/);
  assert.match(html,/Odcinek 1 na 7/);assert.match(html,/Odcinek 3 na 7/);
  assert.match(html,/1A/);assert.match(html,/3B/);
  const allFloors=render(overview.default,{segments:routeSegments(state.cavebot.recording.observed_path.map(p=>p.pos)),floor:7,onFloor:()=>{}});
  assert.match(allFloors,/Przebieg trasy na piętrze 6/);assert.match(allFloors,/Przebieg trasy na piętrze 7/);
  // Default main workspace and inactive editors must not build any map grid.
  for(const props of [{active:true,embedded:true},{active:false}]){
    const closed=render(panel.default,{state,onRefresh:()=>{},...props});
    assert.doesNotMatch(closed,/class="atlas-canvas|Piętro atlasu/);
    assert.match(closed,/Nagranie|nagranie/);
  }
});

test('wide atlas zoom changes scale at every step; Shift horizontal wheel is accepted',()=>{
  assert.deepEqual(atlasWheel(0,false,true,-120),{kind:'zoom',delta:-1});
  for(const [w,h] of [[1200,180],[400,800],[1900,250]]) {
    let oldPixel=Infinity;
    for(let radius=2;radius<=32;radius+=2){
      const grid=atlasViewport(w,h,radius),pixel=w/grid.columns;
      assert.ok(pixel<oldPixel);oldPixel=pixel;
      assert.ok(grid.columns<=65 && grid.rows<=65 && grid.queryRadius<=32);
    }
  }
});
test('closing requires explicit first-point click; insertion and dragging preserve order',()=>{
  const {isRouteClosed,closeRoute,finishAnchorEdit,insertAnchor,removeAnchor}=model.exports;
  const points=[[10,10,7],[15,10,7],[15,10,6]];
  assert.equal(isRouteClosed(points),false);
  const inserted=insertAnchor(points,0,[12,10,7]);
  assert.deepEqual(inserted,[points[0],[12,10,7],points[1],points[2]]);
  assert.equal(insertAnchor(points,0,points[0]),points);
  const closed=finishAnchorEdit(points,0,points[0],false);
  assert.deepEqual(closed,[...points,points[0]]);assert.ok(isRouteClosed(closed));
  assert.equal(closeRoute(closed),closed);
  assert.deepEqual(finishAnchorEdit(points,0,[11,10,7],true),[[11,10,7],points[1],points[2]]);
  const moved=moveAnchor(closed,0,[11,11,7]);
  assert.deepEqual(moved[0],moved.at(-1));assert.ok(isRouteClosed(moved));
  assert.deepEqual(removeAnchor(moved,0),[points[1],points[2],points[1]]);
  assert.deepEqual(removeAnchor(closeRoute(points.slice(0,2)),0),[points[1]]);
  assert.deepEqual(points[0],[10,10,7]);
});
test('active cavebot has separate visible pause and stop controls outside scrolling content',()=>{
  const auth={'@/lib/auth':{authenticatedFetch:()=>{throw Error('render only');}}};
  const bar=component('../app/CavebotRunBar.tsx',auth);
  const chrome=component('../app/DashboardChrome.tsx',{'./CavebotRunBar':bar});
  const React=require('react'),render=require('react-dom/server').renderToStaticMarkup;
  const props={state:{actions:[],adaptive_cavebot:{status:'running',total:12,index:3,name:'Trasa testowa',loops:2,reason:'Nawigacja'}},connected:true,onRefresh:()=>{}};
  let html=render(React.createElement(bar.default,props));
  assert.match(html,/DZIAŁA/);assert.match(html,/Pauza/);assert.match(html,/Zatrzymaj/);
  props.state.adaptive_cavebot.status='paused';html=render(React.createElement(bar.default,props));
  assert.match(html,/PAUZA/);assert.match(html,/Wznów/);
  const shell=render(React.createElement(chrome.default,{...props,view:'automation',mcpConnected:true}));
  assert.ok(shell.indexOf('cavebot-run-bar')<shell.indexOf('workspace-scroll'));
  props.state.adaptive_cavebot.status='idle';assert.equal(render(React.createElement(bar.default,props)),'');
});

test('combat-only target and lure updates occupy the existing status header without extra rows',()=>{
  const Bar=component('../app/CavebotRunBar.tsx',{'@/lib/auth':{}}).default;
  const render=bot=>require('react-dom/server').renderToStaticMarkup(require('react').createElement(Bar,{
    state:{actions:[],adaptive_cavebot:{mode:'combat',status:'running',total:0,combat:bot}},connected:true,onRefresh:()=>{}
  }));
  const idle=render({phase:'route'}), active=render({phase:'fight',target:{name:'Fury Cell',health:28},
    lure:{following:1,goal:4,stop_reason:'transition'}});
  assert.match(idle,/SAMA WALKA/);
  assert.match(active,/cavebot-status-head.*Fury Cell · 28% HP · walka.*W grupie.*Przed przejściem/);
  const routeRow='<span>Automatyczny wybór celu';
  assert.ok(idle.includes(routeRow) && active.includes(routeRow));
  assert.equal(idle.slice(idle.indexOf(routeRow)),active.slice(active.indexOf(routeRow)));
  assert.doesNotMatch(idle,/Fury Cell|W grupie/);
});

test('action menus dismiss on outside pointer or focus, preserve inside interaction and restore focus on Escape',()=>{
  const handlers=new Map(),doc={
    addEventListener:(kind,fn)=>handlers.set(kind,fn),
    removeEventListener:(kind,fn)=>{assert.equal(handlers.get(kind),fn);handlers.delete(kind);}
  };
  let cleanup,focused=false;
  const menu={ownerDocument:doc,open:true,querySelector:()=>({focus:()=>{focused=true;}})};
  const Menu=component('../app/ActionMenu.tsx',{react:{useRef:()=>({current:menu}),useEffect:fn=>{cleanup=fn();}}}).default;
  const ui=Menu({className:'profile-more',label:'Menu',icon:'…',title:'Menu'});
  assert.equal(ui.props.name,'panel-action-menu');
  handlers.get('pointerdown')({composedPath:()=>[menu]});assert.ok(menu.open);
  handlers.get('pointerdown')({composedPath:()=>[doc]});assert.equal(menu.open,false);
  menu.open=true;handlers.get('focusin')({composedPath:()=>[doc]});assert.equal(menu.open,false);
  menu.open=true;ui.props.onKeyDown({key:'Escape',currentTarget:menu,stopPropagation(){}});
  assert.ok(focused && !menu.open);cleanup();assert.equal(handlers.size,0);
});

test('navigation hold is displayed independently from active combat and healing',()=>{
  const bar=component('../app/CavebotRunBar.tsx',{'@/lib/auth':{authenticatedFetch:()=>{throw Error('render only');}}});
  const React=require('react'),render=require('react-dom/server').renderToStaticMarkup;
  const state={actions:[{name:'auto_potions',running:true}],adaptive_cavebot:{status:'running',total:3,reason:'Ruch czeka',
    navigation_hold:'Brak potwierdzenia przejścia',domains:{movement:{status:'held',reason:'Brak potwierdzenia przejścia'},combat:{status:'running',reason:null}}}};
  const html=render(React.createElement(bar.default,{state,connected:true,onRefresh:()=>{}}));
  assert.match(html,/Ruch: wstrzymany/);assert.match(html,/Walka: działa/);assert.match(html,/Mikstury: działa/);
  assert.match(html,/Sprawdź przejście ponownie/);assert.match(html,/Zatrzymaj/);
});

test('stair selection adds approach and exit, repairs a stair anchor, and undo preserves previous floor',()=>{
  const {appendTransition,rememberEdit,unresolvedLegs,undoShortcut}=model.exports;
  const edge={origin:[12,10,7],entry:[12,9,7],exit:[12,8,6]};
  const before=[[10,10,7],[12,10,7],[12,9,7]],center=[12,10,7];
  const after=appendTransition(before,edge);
  assert.deepEqual(after,[[10,10,7],[12,10,7],[12,8,6]]);
  const history=rememberEdit([],before,center);before[0][0]=99;center[2]=5;
  assert.deepEqual(history[0].points[0],[10,10,7]);assert.equal(history[0].center[2],7);
  assert.deepEqual(unresolvedLegs({points:history[0].points,gaps:[{segment:1}]}),[[[12,10,7],[12,9,7]]]);
  assert.ok(undoShortcut('z',true,false,'svg',false));
  for(const tag of ['INPUT','TEXTAREA','SELECT'])assert.ok(!undoShortcut('z',true,false,tag,false));
  assert.ok(!undoShortcut('z',true,false,'DIV',true));
  assert.ok(!undoShortcut('z',true,true,'svg',false));
});

test('target preview consumes the real control envelope and rejects invalid payloads before render',()=>{
  const {parseTargetPreview}=component('../lib/cavebotModel.ts');
  const result={message:'Pasywny podgląd',targets:[{id:123,name:'Wolf',health:70,priority:90,distance:2}],selected:{id:123}};
  assert.deepEqual(parseTargetPreview({ok:true,message:result}),result);
  assert.deepEqual(parseTargetPreview(result),result);
  assert.deepEqual(parseTargetPreview({ok:true,message:{message:'Brak celów',targets:[]}}).targets,[]);
  for(const value of [null,{}, {message:{message:{},targets:[]}}, {message:{message:'x',targets:[{}]}}])assert.throws(()=>parseTargetPreview(value));
});

test('lure status distinguishes confirmed followers from group, timeout and transition stops',()=>{
  const React=require('react'),render=require('react-dom/server').renderToStaticMarkup;
  const panel=component('../app/LureStatus.tsx');
  const progress={following:0,goal:4,waiting:false,stop_reason:null};
  const html=value=>render(React.createElement(panel.default,{progress:{...progress,...value}}));
  assert.match(html({}),/W grupie/);assert.match(html({}),/Zbieram grupę/);
  assert.equal((html({following:2}).match(/class="filled"/g)||[]).length,2);
  assert.match(html({waiting:true}),/Czekam na moby · atakuję/);
  assert.match(html({following:3,stop_reason:'group'}),/Grupa zebrana · walczę/);
  assert.equal((html({following:3,stop_reason:'group'}).match(/class="filled"/g)||[]).length,3);
  assert.match(html({stop_reason:'timeout'}),/Limit czasu · walczę/);
  assert.doesNotMatch(html({stop_reason:'timeout'}),/Grupa zebrana/);
  assert.match(html({stop_reason:'transition'}),/Przed przejściem/);
  assert.match(html({engaged:4,goal:8,stop_reason:'corridor'}),/Przed wąskim korytarzem · walczę/);
  const paused=render(React.createElement(panel.default,{progress,paused:true}));
  assert.match(paused,/Lurowanie wstrzymane/);assert.doesNotMatch(paused,/atakuję|walczę/);
  const settings=render(React.createElement(panel.LureSettings,{value:component('../lib/cavebotModel.ts').defaultCombat(),onChange:()=>{throw Error('render cannot start anything');}}));
  assert.match(settings,/Liczba mobów do lurowania[^>]*value="4"/);
  assert.match(settings,/mobach w grupie/);
  assert.equal((html({following:1,engaged:4,nearby:3}).match(/class="filled"/g)||[]).length,4);
});

test('one Start uses movement only when enabled and waypoints exist, retaining combat preferences',()=>{
  const {defaultCombat,usesRoute,v2StartRequest}=component('../lib/cavebotModel.ts');
  const combat={...defaultCombat(),enabled:true,mode:'lure',rules:[{pattern:'Wolf*',action:'attack',priority:80}],spells:[{words:'example',trigger:'visible',cooldown:2,ki:10,range:5,count:1}]};
  const route={id:'draft',points:[[10,10,7],[11,10,7],[10,10,7]],combat};
  assert.equal(usesRoute(true,route),true);assert.equal(usesRoute(false,route),false);assert.equal(usesRoute(true,undefined),false);
  assert.deepEqual(v2StartRequest(true,route,combat,true,'scope'),{command:'start',scope:'scope',draft_id:'draft',loop:true});
  for(const [movement,draft] of [[false,route],[true,undefined],[true,{...route,points:[]}]]){
    const request=v2StartRequest(movement,draft,combat,true,'scope');
    assert.equal(request.command,'start_combat');assert.equal(request.combat.mode,'stop');
    assert.deepEqual(request.combat.rules,combat.rules);assert.deepEqual(request.combat.spells,combat.spells);assert.equal(request.draft_id,undefined);
  }
  assert.equal(combat.mode,'lure');
  const open={...route,points:route.points.slice(0,2)};
  assert.equal(v2StartRequest(true,open,combat,true,'scope').command,'start');
  // The backend validates automatic return/inverse transitions using terrain.
  assert.equal(v2StartRequest(true,{...open,points:[[10,10,7],[13,10,7]]},combat,true,'scope').command,'start');
  assert.equal(v2StartRequest(true,{...open,points:[[10,10,7],[10,10,6]]},combat,true,'scope').command,'start');
  assert.equal(v2StartRequest(false,open,combat,true,'scope').command,'start_combat');
  assert.throws(()=>v2StartRequest(false,route,{...combat,enabled:false},true,'scope'),/Włącz walkę/);
  assert.throws(()=>v2StartRequest(true,undefined,{...combat,rules:[]},true,'scope'),/dodaj potwory/);
  assert.equal(v2StartRequest(true,route,{...combat,enabled:false},true,'scope').command,'start');
});

test('main workspace has one Start, a default-on movement checkbox, and always-visible waypoints',()=>{
  const workspace=component('../app/CavebotWorkspace.tsx',{
    '@/lib/cavebotModel':component('../lib/cavebotModel.ts'),
    '@/lib/auth':{authenticatedFetch:()=>{throw Error('render must not start actions');}},
    './AtlasPanel':{default:props=>require('react').createElement('div',null,props.routeEnabledControl,'Waypoint editor')},
    './SpellPicker':spellPicker,'./SpellConditions':component('../app/SpellConditions.tsx'),
    './LureStatus':component('../app/LureStatus.tsx'),
    './MonsterRules':component('../app/MonsterRules.tsx',{'@/lib/cavebotModel':component('../lib/cavebotModel.ts'),'./SpellPicker':spellPicker,'./SpellConditions':component('../app/SpellConditions.tsx')}),
  });
  const html=require('react-dom/server').renderToStaticMarkup(require('react').createElement(workspace.default,{state:null,active:true,onRefresh:()=>{}}));
  assert.equal((html.match(/▶ Uruchom/g)||[]).length,1);
  assert.match(html,/type="checkbox"[^>]*checked/);assert.match(html,/Aktywuj ruch/);assert.match(html,/Aktywuj walkę/);assert.match(html,/Brak waypointów/);
  assert.match(html,/Waypoint editor/);assert.doesNotMatch(html,/Trasa \+ walka|Start samej walki|v2-mode-switch/);
});

test('combat can start with an invalid unsaved sketch only when route movement is off',async()=>{
  const host=hookHost(),requests=[],Atlas=()=>null,cm=component('../lib/cavebotModel.ts');
  const route={id:'invalid',name:'Unfinished',points:[[10,10,7],[10,10,8]],links:[],labels:[],loop:true,combat:cm.defaultCombat()};
  const Workspace=component('../app/CavebotWorkspace.tsx',{
    react:host.react,'@/lib/cavebotModel':cm,'@/lib/auth':{authenticatedFetch:async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;requests.push({url,body});
      return {ok:true,json:async()=>({scope:'scope',drafts:[route],trash:[]})};
    }},'./AtlasPanel':{default:Atlas},'./SpellPicker':spellPicker,'./SpellConditions':component('../app/SpellConditions.tsx'),
    './LureStatus':component('../app/LureStatus.tsx'),'./MonsterRules':{default:()=>null},
  }).default;
  const props={active:true,onRefresh:()=>{},state:{connected:true,automation_paused:false,cavebot:{recording:{active:false}}}};
  const render=()=>elements(host.render(Workspace,props));
  await render().find(n=>n.type===Atlas).props.onDraftSaved('invalid');requests.length=0;
  let nodes=render();nodes.find(n=>n.type===Atlas).props.onEditingChange(true);
  nodes.find(n=>n.props.label==='Aktywuj walkę').props.onChange(true);
  // Supply a matching monster through the actual rule editor callback.
  nodes=render();nodes.find(n=>Array.isArray(n.props.rules)).props.onChange([{pattern:'Wolf*',action:'attack',priority:50}]);
  let atlas=render().find(n=>n.type===Atlas);
  assert.equal(render().find(n=>n.props.className==='v2-start').props.disabled,true,'route still requires saved points/configuration');
  const checkbox=atlas.props.routeEnabledControl;
  assert.equal(checkbox.props.checked,true);checkbox.props.onChange(false);
  nodes=render();assert.ok(nodes.some(n=>n.props.role==='status'&&JSON.stringify(n.props.children).includes('Niezapisany szkic')));
  const start=nodes.find(n=>n.props.className==='v2-start');assert.equal(start.props.disabled,false);
  assert.equal(nodes.find(n=>n.type==='button'&&n.props.children==='Zapisz walkę').props.disabled,false);
  await start.props.onClick();assert.equal(requests.length,1);
  assert.equal(requests[0].body.command,'start_combat');assert.equal(requests[0].body.combat.mode,'stop');assert.equal(requests[0].body.draft_id,undefined);
  assert.deepEqual(requests[0].body.combat.rules,[{pattern:'Wolf*',action:'attack',priority:50}]);
  assert.ok(render().some(n=>n.props.role==='status'&&JSON.stringify(n.props.children).includes('Niezapisany szkic')),'starting combat preserves the unsaved sketch');
  props.state.automation_paused=true;assert.equal(render().find(n=>n.props.className==='v2-start').props.disabled,true);
});

test('monster rules default to select-and-attack with optional per-monster settings',()=>{
  const model=component('../lib/cavebotModel.ts'),React=require('react'),render=require('react-dom/server').renderToStaticMarkup;
  const original=model.defaultMonsterRule();original.pattern='Wolf*';
  const rules=[original,{...model.defaultMonsterRule(),pattern:'Dragon',stance:'distance',distance:3,spell_mode:'custom',spell:{...model.defaultCombatSpell(),words:'Only Spell'}}];
  const panel=component('../app/MonsterRules.tsx',{'@/lib/cavebotModel':model,'./SpellPicker':spellPicker,'./SpellConditions':component('../app/SpellConditions.tsx')});
  const props={rules,movement:true,onChange:()=>{throw Error('render must not save');}};
  const html=render(React.createElement(panel.default,props));
  assert.match(html,/value="attack" selected=""/);assert.match(html,/Zaznacz i atakuj/);
  assert.match(html,/Dystans 3 SQM · Only Spell/);assert.doesNotMatch(html,/aria-label="Czar potwora/);
  // Expand the second monster, using the real component with a controlled hook.
  const expanded=component('../app/MonsterRules.tsx',{'@/lib/cavebotModel':model,'./SpellPicker':spellPicker,'./SpellConditions':component('../app/SpellConditions.tsx'),react:{...React,useState:()=>[1,()=>{}]}});
  const details=render(React.createElement(expanded.default,{...props,movement:false}));
  assert.match(details,/aria-label="Czar potwora 2"/);assert.match(details,/>Only Spell<\/span>/);assert.match(details,/aria-haspopup="dialog"/);assert.match(details,/Ruch ręczny/);
  assert.doesNotMatch(details,/aria-label="Czar potwora 1"/);
  const request=model.v2StartRequest(false,undefined,{...model.defaultCombat(),enabled:true,rules},false,'');
  assert.deepEqual(request.combat.rules,rules);assert.equal(request.combat.mode,'stop');
});

test('fast simulation checks all floors and transitions immediately; animation is bounded',()=>{
  const {simulationReport,fastSimulationNext}=model.exports;
  const points=[[10,10,7],[10,10,6],[11,10,6],[10,10,7]];
  const plan={path:points,complete:true,gaps:[],transitions:[{action:'step'},{action:'use_then_step'},{action:'use'}]};
  const report=simulationReport(plan,true,points);
  assert.equal(report.ok,true);assert.deepEqual(report.floors,[7,6,7]);assert.equal(report.transitions,3);
  assert.equal(simulationReport({...plan,transitions:[{action:'use_with'}]},true,points).ok,false);
  assert.equal(simulationReport({...plan,complete:false,gaps:[{reason:'Brak przejścia'}]},false,points).ok,false);
  assert.equal(simulationReport(plan,true,points.slice(0,-1)).ok,false);
  for(const length of [2,100,20000]){
    let index=0,frames=0;while(index<length-1){index=fastSimulationNext(index,length);frames++;assert.ok(frames<=60);}
    assert.equal(index,length-1);
  }
});

test('atlas initialization retries 503, clears waiting after success and cancels stale loads',async()=>{
  const {readAtlasWhenReady}=component('../lib/atlasLoading.ts');
  let attempts=0;const states=[];
  const data=await readAtlasWhenReady(async()=>{if(++attempts<3)throw {status:503};return {drafts:['saved']};},v=>states.push(v),()=>false,async()=>{});
  assert.deepEqual(data,{drafts:['saved']});assert.deepEqual(states,[true,true,false]);assert.equal(attempts,3);
  let cancelled=false;attempts=0;
  assert.equal(await readAtlasWhenReady(async()=>{attempts++;throw {status:503};},()=>{},()=>cancelled,async()=>{cancelled=true;}),undefined);
  assert.equal(attempts,1);
  for(const status of [400,401,403,500])await assert.rejects(readAtlasWhenReady(async()=>{throw {status};},()=>{},()=>false,async()=>{throw Error('must not retry');}),e=>e.status===status);
});


test('persisted sketch fingerprint stays clean after save/reload and undo, not after edits',()=>{
  const {draftFingerprint}=model.exports;
  const points=[[10,10,7],[11,10,7]],links=[{from_index:0,id:'door'}];
  const saved=draftFingerprint('Route',points,['Start',''],links,true);
  assert.equal(draftFingerprint(' Route ',JSON.parse(JSON.stringify(points)),[' Start ',''],[...links],true),saved);
  assert.notEqual(draftFingerprint('Route',points,['Changed',''],links,true),saved);
  assert.notEqual(draftFingerprint('Route',points,['Start',''],[],true),saved);
  assert.equal(draftFingerprint('Route',points,['Start',''],links,false),saved);
  assert.equal(draftFingerprint('Route',points,['Start',''],links,true),saved);
});

test('near endpoints require a server-verified return before simulation succeeds',()=>{
  const {simulationReport}=model.exports,points=[[10,10,7],[11,11,7]];
  const plan={path:[...points,points[0]],complete:true,loop_closed:true,gaps:[],transitions:[]};
  assert.equal(simulationReport(plan,true,points).ok,true);
  assert.equal(simulationReport({...plan,loop_closed:false},true,points).ok,false);
});

test('an earlier route gap does not falsely diagnose the unvisited closing leg',()=>{
  const points=[[10,10,7],[20,20,6],[11,10,7]];
  const report=model.exports.simulationReport({path:points.slice(0,1),complete:false,loop_closed:false,gaps:[{reason:'Missing ladder'}],transitions:[]},true,points);
  assert.equal(report.ok,false);assert.ok(report.issues.includes('Missing ladder'));
  assert.ok(!report.issues.some(x=>x.includes('domknięcia')));
});

test('deleting all saved points remains dirty; an empty new sketch is clean',()=>{
  const {hasDraftChanges,draftFingerprint}=model.exports,points=[[10,10,7],[11,10,7]];
  const saved=draftFingerprint('Route',points,[],[],true);
  assert.equal(hasDraftChanges('id',saved,'Route',[],[],[],true),true);
  assert.equal(hasDraftChanges('id',saved,'Route',points,[],[],true),false);
  assert.equal(hasDraftChanges('','','',[],[],[],true),false);
});

// Exercise actual event handlers against a stateful hook host, without a live
// Electron window, backend, game input, DOM focus or writes to user drafts.
function hookHost(){
  const values=[];let cursor=0;
  const React=require('react');
  const host={...React,useEffect:()=>{},useLayoutEffect:()=>{},useMemo:(compute,deps)=>{
    const i=cursor++,previous=values[i];
    if(!previous||deps.some((value,k)=>!Object.is(value,previous.deps[k])))values[i]={deps,value:compute()};
    return values[i].value;
  },useState:initial=>{
    const i=cursor++;if(!(i in values))values[i]=typeof initial==='function'?initial():initial;
    return [values[i],v=>{values[i]=typeof v==='function'?v(values[i]):v;}];
  },useRef:initial=>{const i=cursor++;if(!(i in values))values[i]={current:initial};return values[i];}};
  return {react:host,render:(C,props)=>{cursor=0;return C(props);}};
}
function elements(tree){
  const result=[];function walk(node){if(Array.isArray(node)){node.forEach(walk);return;}if(!node||typeof node!=='object')return;
    if(node.props){result.push(node);walk(node.props.children);}}
  walk(tree);return result;
}

test('adaptive recording starts from waypoint options and stopping produces an unsaved hunt sketch',async()=>{
  const host=hookHost(), requests=[];
  const points=[[100,101,7],[110,101,7],[100,101,7]];
  const props={active:true,embedded:true,onRefresh:()=>{},state:{connected:true,
    passive_map:{center:points[0],annotation_scope:'scope',cells:[]},
    cavebot:{recording:{active:false},recordings:[]}}};
  const Panel=component('../app/AtlasPanel.tsx',{
    react:host.react,'@/lib/atlasModel':model.exports,'@/lib/atlasLoading':component('../lib/atlasLoading.ts'),
    '@/lib/cavebotModel':component('../lib/cavebotModel.ts'),'@/lib/mapVisuals':{tileStyles:{},navigationStyles:{}},
    './PassiveMapPanel':{},'./TransitionLearningPanel':{},'./AtlasRouteOverview':{default:()=>null},
    './MapTools':component('../app/MapTools.tsx'),'next/dynamic':{default:()=>()=>null},
    '@/lib/auth':{authenticatedFetch:async(url,options)=>{
      const body=JSON.parse(options.body);requests.push({url,body});
      const plan={scope:'scope',points,path:points,original:points,links:[],transitions:[],gaps:[],complete:true,
        adaptive_evidence:{areas:2,samples:21,total_samples:66,xp:500}};
      return {ok:true,json:async()=>url.endsWith('/plan')?plan:{ok:true}};
    }},
  }).default;
  const nodes=()=>elements(host.render(Panel,props));
  const start=nodes().find(n=>n.props.children==='Nagrywanie adaptacyjne');
  assert.ok(!start.props.disabled);await start.props.onClick();
  const {name,mode}=requests[0].body;assert.match(name,/^exp_/);assert.equal(mode,'adaptive');
  props.state.cavebot.recording={active:true,name,mode,observed_path:[],position_count:21};
  for(const stage of [0,1,2,3,4]) {
    props.state.cavebot.recording.hunt_progress={stage};
    const view=nodes();
    assert.equal(view.find(n=>n.type==='progress').props.value,stage);
    assert.equal(view.some(n=>n.props['data-ready']===true),stage===4);
    assert.ok(!view.find(n=>n.props.label==='Zakończ nagranie').props.disabled);
  }
  await nodes().find(n=>n.props.label==='Zakończ nagranie').props.onClick();
  props.state.cavebot.recording={active:false};props.state.cavebot.recordings=[{name,mode}];
  assert.deepEqual(requests.map(r=>new URL(r.url).pathname),['/api/cavebot/record/start','/api/cavebot/record/stop','/api/map/plan']);
  assert.equal(requests[2].body.recording,name);
  assert.ok(!nodes().find(n=>n.props.label==='Zapisz szkic').props.disabled);
  assert.ok(!nodes().some(n=>n.type==='progress'));
});

test('combat profile selection and discard never mutate waypoint geometry',async()=>{
  const host=hookHost(),requests=[],Atlas=()=>null,cm=component('../lib/cavebotModel.ts');
  const route={id:'route',name:'Route',points:[[10,10,7],[11,10,7]],links:[],labels:[],loop:true,combat:cm.defaultCombat()};
  const current={...cm.defaultCombat(),enabled:true,rules:[{pattern:'Wolf*',priority:70,action:'attack'}]};
  const bound={...current,lure_count:3,mode:'lure'};
  const Workspace=component('../app/CavebotWorkspace.tsx',{
    react:host.react,'@/lib/cavebotModel':cm,'@/lib/auth':{authenticatedFetch:async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;requests.push({url,body});
      return {ok:true,json:async()=>url.endsWith('/profiles')?{message:{config:bound}}:{scope:'scope',drafts:[route],trash:[]}};
    }},'./AtlasPanel':{default:Atlas},'./SpellPicker':spellPicker,'./SpellConditions':component('../app/SpellConditions.tsx'),
    './LureStatus':component('../app/LureStatus.tsx'),'./MonsterRules':{default:()=>null},
  }).default;
  const props={active:true,onRefresh:()=>{},state:{connected:true,actions:[],combat_profile:current,profile_selection:{routes:{'scope:route':'profile'}},cavebot:{recording:{active:false}}}};
  const render=()=>elements(host.render(Workspace,props));
  await render().find(n=>n.type===Atlas).props.onDraftSaved('route');
  let atlas=render().find(n=>n.type===Atlas);
  const select=elements(atlas.props.routePicker).find(n=>n.type==='select');
  select.props.onChange({target:{value:'route'}});await new Promise(resolve=>setImmediate(resolve));
  const picker=render().find(n=>n.props.kind==='combat');
  assert.deepEqual(picker.props.config,bound);
  const combatSection=render().find(n=>n.props.className==='v2-combat');
  assert.ok(elements(combatSection).some(n=>n.props.kind==='combat'),'profile belongs to the combat column');
  render().find(n=>Array.isArray(n.props.rules)).props.onChange([{pattern:'Dragon*',priority:90,action:'attack'}]);
  render().find(n=>n.type==='button'&&n.props.children==='Odrzuć zmiany').props.onClick();
  assert.deepEqual(render().find(n=>n.props.kind==='combat').props.config,current);
  assert.equal(render().find(n=>n.type===Atlas).props.initialDraftId,'route');
  assert.deepEqual(requests.filter(r=>r.body),[{url:'http://127.0.0.1:8089/api/profiles',body:{command:'apply',kind:'combat',id:'profile'}}]);
});

test('drawing and PPM edit an unsaved sketch; only explicit Save persists it',async()=>{
  const host=hookHost(),requests=[];const TileMenu=()=>null;
  const props={active:true,onRefresh:()=>{},state:{connected:true,passive_map:{center:[100,100,7],annotation_scope:'scope',cells:[]},cavebot:{recording:{active:false},recordings:[]}}};
  const Panel=component('../app/AtlasPanel.tsx',{
    react:host.react,'@/lib/atlasModel':model.exports,'@/lib/atlasLoading':component('../lib/atlasLoading.ts'),
    '@/lib/auth':{authenticatedFetch:async(url,options)=>{const body=JSON.parse(options.body);requests.push({url,body});
      const data=url.endsWith('/drafts')?{saved_id:'saved',drafts:[{...body,id:'saved',updated:1}]}:{...body,original:body.points,path:body.points,complete:true,loop_closed:false,gaps:[],transitions:[],links:body.links??[]};
      return {ok:true,json:async()=>data};}},
    '@/lib/cavebotModel':component('../lib/cavebotModel.ts'),'@/lib/mapVisuals':{tileStyles:{},navigationStyles:{}},
    './PassiveMapPanel':{TileContextMenu:TileMenu,NavigationDetails:()=>null},'./TransitionLearningPanel':{transitionActions:{}},
    './AtlasRouteOverview':{default:()=>null},'./MapTools':component('../app/MapTools.tsx'),'next/dynamic':{default:()=>()=>null},
  }).default;
  let tree;
  const render=()=>{tree=host.render(Panel,props);return elements(tree);};
  let nodes=render();nodes.find(n=>n.props.label==='Edytuj waypointy').props.onClick();
  for(const x of [350,378]){
    const svg=render().find(n=>n.type==='svg');svg.props.ref.current={getBoundingClientRect:()=>({left:0,right:700,top:0,bottom:450,width:700,height:450})};
    svg.props.onPointerDown({button:0,ctrlKey:false,clientX:x,clientY:224});
    await new Promise(resolve=>setImmediate(resolve));
  }
  nodes=render();const save=nodes.find(n=>n.props.className==='map-save-draft');
  assert.ok(nodes.some(n=>n.props['aria-label']==='Widoczność etapu 1'),'stage toggles remain visible while drawing');
  assert.equal(save.props.disabled,false);assert.equal(requests.filter(r=>r.url.endsWith('/drafts')).length,0);
  const svg=nodes.find(n=>n.type==='svg');svg.props.onContextMenu({preventDefault:()=>{},clientX:350,clientY:224});
  nodes=render();const menu=nodes.find(n=>n.type===TileMenu);
  assert.deepEqual(menu.props.waypoints,[0]);assert.equal(menu.props.routeLocked,false);
  assert.equal(nodes.find(n=>n.props.className==='map-save-draft').props.disabled,false,'PPM must not remove the first point');
  // Explicit menu deletion, then Ctrl+Z undo, remains local as well.
  menu.props.onRemoveWaypoint(0);nodes=render();
  assert.equal(nodes.find(n=>n.props.className==='map-save-draft').props.disabled,true);
  nodes.find(n=>n.props.label==='Cofnij zmianę · Ctrl + Z').props.onClick();
  await new Promise(resolve=>setImmediate(resolve));
  await render().find(n=>n.props.className==='map-save-draft').props.onClick();
  nodes=render();assert.equal(requests.filter(r=>r.url.endsWith('/drafts')).length,1);
  assert.equal(requests.at(-1).body.name,'Nowa trasa');assert.equal(requests.at(-1).body.points.length,2);
  assert.equal(nodes.find(n=>n.props.className==='map-save-draft').props.disabled,true);
  const name=nodes.find(n=>n.props['aria-label']==='Nazwa edytowanego szkicu');name.props.onChange({target:{value:'Edited'}});
  nodes=render();assert.equal(nodes.find(n=>n.props.className==='map-save-draft').props.disabled,false);
  nodes.find(n=>n.type==='button'&&n.props.children==='Odrzuć zmiany').props.onClick();
  await new Promise(resolve=>setImmediate(resolve));
  nodes=render();assert.equal(nodes.find(n=>n.props['aria-label']==='Nazwa edytowanego szkicu').props.value,'Nowa trasa');
  assert.equal(nodes.find(n=>n.props.className==='map-save-draft').props.disabled,true);
  assert.equal(requests.filter(r=>r.url.endsWith('/drafts')).length,1);
  // Manual map navigation interrupts following without changing the route.
  const canvas=nodes.find(n=>n.type==='svg');
  const target={getBoundingClientRect:()=>({width:700}),setPointerCapture:()=>{},hasPointerCapture:()=>true,releasePointerCapture:()=>{}};
  assert.equal(nodes.find(n=>n.props.label==='Śledź postać').props['aria-pressed'],true);
  canvas.props.onPointerDown({button:0,ctrlKey:true,clientX:350,clientY:224,currentTarget:target,pointerId:1});
  nodes=render();assert.equal(nodes.find(n=>n.props.label==='Śledź postać').props['aria-pressed'],false);
  nodes.find(n=>n.type==='svg').props.onPointerMove({clientX:378,clientY:224,currentTarget:target});
  render().find(n=>n.type==='svg').props.onPointerUp({currentTarget:target,pointerId:1});
  nodes=render();assert.equal(nodes.find(n=>n.props.className==='map-save-draft').props.disabled,true);
  nodes.find(n=>n.props.label==='Śledź postać').props.onClick();
  nodes=render();nodes.find(n=>n.props['aria-label']==='Piętro atlasu').props.onChange({target:{value:'6'}});
  nodes=render();assert.equal(nodes.find(n=>n.props.label==='Śledź postać').props['aria-pressed'],false);
  assert.equal(nodes.find(n=>n.props['aria-label']==='Piętro atlasu').props.value,6);
});

test('tile menu offers annotation and explicit waypoint deletion, with route lock',()=>{
  const host=hookHost(),removed=[];let closed=0;
  const previous=global.document;global.document={body:{}};
  try {
    const Menu=component('../app/PassiveMapPanel.tsx',{
      react:host.react,'react-dom':{createPortal:node=>node},'@/lib/api':{saveMapAnnotation:()=>{throw Error('must not save');}},
      '@/lib/mapInteractions':component('../lib/mapInteractions.ts'),'@/lib/mapVisuals':component('../lib/mapVisuals.ts'),
      './TransitionLearningPanel':{transitionActions:{}},
    }).TileContextMenu;
    const props={position:{key:'1,2,7',x:0,y:0},cell:{...cell(1,2,7),items:[{id:1948,stack:1,flags:[]}]},editable:true,onClose:()=>closed++,onCopy:()=>{},waypoints:[0,4],onRemoveWaypoint:i=>removed.push(i)};
    let nodes=elements(host.render(Menu,props));
    assert.equal(removed.length,0);assert.equal(nodes.filter(n=>n.props.role==='menuitem'&&n.props.children==='Drabina').length,0);
    const labels=nodes.filter(n=>n.props.role==='menuitem').map(n=>JSON.stringify(n.props.children));
    assert.ok(labels.some(t=>t.includes('Drabina')));assert.ok(labels.some(t=>t.includes('Edytuj opis')));
    nodes.find(n=>n.props.className==='tile-menu-delete').props.onClick();assert.deepEqual(removed,[0]);assert.equal(closed,1);
    nodes=elements(host.render(Menu,{...props,routeLocked:true}));
    assert.ok(nodes.filter(n=>n.props.className==='tile-menu-delete').every(n=>n.props.disabled));
  } finally {global.document=previous;}
});

test('direction markers follow forward and return legs but never cross a floor gap',()=>{
  const arrows=model.exports.directionMarkers([[0,0,7],[8,0,7],[8,0,6],[0,0,6]]);
  assert.deepEqual(arrows.map(a=>a.angle),[0,0,180,180]);
  assert.ok(arrows.every(a=>a.y===0&&a.x>0&&a.x<8));
  assert.deepEqual(model.exports.directionMarkers([[1,1,7],[1,1,7],[9,9,6]]),[]);
});

test('transition choices collapse duplicate approaches and show an unambiguous target floor',()=>{
  const entry=[10,10,7],edge={id:'a',origin:[9,10,7],entry,exit:[10,11,6],action:'use',item_id:1948};
  const near={...edge,id:'near',origin:[10,11,7]};
  const choices=model.exports.transitionChoices([edge,near,{...near,id:'more',count:3}],entry,[10,12,7]);
  assert.equal(choices.length,1);assert.equal(choices[0].id,'more');
  assert.equal(model.exports.transitionDestination(entry,choices),'↑ 6');
  assert.equal(model.exports.transitionDestination(entry,[edge,{...near,exit:[20,20,5]}]),'');
});

test('one conditional digging choice covers the same observed shovel and AiO tools',()=>{
  const edge={id:'shovel',origin:[10,11,7],entry:[10,10,7],exit:[10,10,8],action:'use_with_then_step',item_id:593,prepared_item_id:594,tool_id:3457,tool_ids:[3457,8900],count:1};
  const rows=[edge,{...edge,id:'aio',tool_id:8900,tool_ids:[8900,3457],count:3}];
  assert.equal(model.exports.transitionChoices(rows,edge.entry).length,1);
  assert.equal(model.exports.transitionChoices(rows,edge.entry)[0].id,'aio');
  for(const other of [{...edge,exit:[20,20,8]},{...edge,action:'use'},{...edge,prepared_item_id:607},{...edge,tool_ids:[8899]}])
    assert.equal(model.exports.transitionChoices([...rows,other],edge.entry).length,2,'different observed outcomes/mechanisms stay separate');
});

test('transition preview connects the diagonal trigger step and merges repeat markers without editing the route',()=>{
  const edge={origin:[10,11,7],entry:[11,10,7],exit:[11,10,8],action:'step'};
  const back={origin:[12,10,8],entry:[12,10,8],exit:[10,11,7],action:'use'};
  const path=[edge.origin,edge.exit,back.origin,back.exit,edge.exit],transitions=[edge,back,{...edge}];
  const snapshot=JSON.stringify({path,transitions});
  let arrows=model.exports.transitionApproaches(path,transitions,7);
  assert.equal(arrows.length,1);assert.deepEqual(arrows[0].from,edge.origin);assert.deepEqual(arrows[0].to,edge.entry);assert.equal(arrows[0].angle,-45);
  assert.equal(model.exports.transitionApproaches(path,transitions,7,[0])[0].index,2);
  assert.equal(model.exports.transitionApproaches(path,transitions,8).length,0,'use does not draw a fictitious walking step');
  const markers=model.exports.transitionMarkers(path,transitions,8);
  assert.deepEqual(markers.find(m=>!m.out).indices,[0,2]);
  assert.deepEqual(model.exports.transitionMarkers(path,transitions,8,[1]).find(m=>!m.out).indices,[2]);
  const ends=model.exports.endpointMarkers(path,8);
  assert.deepEqual(ends.find(m=>m.pos.join(',')===edge.exit.join(',')).labels,['2A','4A/B']);
  assert.equal(JSON.stringify({path,transitions}),snapshot);
});

test('route blockers name the exact transition and avoided tile; a recognized downward step is supported',()=>{
  const entry=[10,10,8],exit=[10,10,9];
  const issues=model.exports.routeExecutionIssues({transitions:[
    {origin:[9,11,8],entry,exit,action:'step'},
    {origin:[15,15,9],entry:[15,15,9],exit:[15,16,8],action:'use_with',tool_id:8900},
  ],avoid_tiles:[[18,12,9],[18,12,9]]});
  assert.equal(issues.length,2);assert.match(issues[0].message,/T2.*8900.*nieobsługiwane/);assert.deepEqual(issues[0].pos,[15,15,9]);
  assert.match(issues[1].message,/18, 12, 9.*blokuje/);
  const dig={origin:[10,11,7],entry:[10,10,7],exit:[10,10,8],action:'use_with_then_step',item_id:593,prepared_item_id:594,tool_id:3457};
  assert.deepEqual(model.exports.routeExecutionIssues({transitions:[dig]}),[]);
  assert.equal(model.exports.routeExecutionIssues({transitions:[{...dig,origin:[10,10,7]}]}).length,1);
});

test('web traversal in the preview requires the confirmed web, an AiO and a single step on the same floor',()=>{
  const web={origin:[10,10,7],entry:[11,10,7],exit:[11,10,7],action:'clear_web',item_id:183,tool_id:8899};
  for(const item_id of [182,183]){
    assert.deepEqual(model.exports.routeExecutionIssues({transitions:[{...web,item_id},{...web,item_id,tool_id:8900}]}),[]);
  }
  for(const change of [{item_id:184},{tool_id:3457},{exit:[11,10,8]},{origin:[9,10,7]}]){
    assert.equal(model.exports.supportedTransition({...web,...change}),false);
  }
});

test('recorded rope ascent is executable with AiO or rope; preview and simulation agree',()=>{
  const base={origin:[10,10,9],entry:[11,10,9],exit:[11,11,8],action:'use_with',item_id:386,tool_id:8900};
  const plan={path:[base.origin,base.exit],points:[base.origin,base.exit],transitions:[base],gaps:[],complete:true};
  for(const tool_id of [3003,8899,8900]){
    const edge={...base,tool_id};
    assert.equal(model.exports.supportedTransition(edge),true);
    assert.deepEqual(model.exports.routeExecutionIssues({transitions:[edge]}),[]);
    assert.equal(model.exports.simulationReport({...plan,transitions:[edge]},false,plan.points).ok,true);
  }
  for(const patch of [{tool_id:3457},{item_id:null},{item_id:0},{exit:[11,11,9]},{origin:[8,10,9]},{action:'fly_up'}]){
    const edge={...base,...patch};
    assert.equal(model.exports.supportedTransition(edge),false);
    assert.equal(model.exports.routeExecutionIssues({transitions:[edge]}).length,1);
    assert.equal(model.exports.simulationReport({...plan,transitions:[edge]},false,plan.points).ok,false);
  }
});

test('repairing a known rope link keeps its recorded approach after learning from another side',()=>{
  const a=[10,10,9],entry=[11,10,9],b=[11,11,8];
  const edge={id:'rope',origin:[11,11,9],entry,exit:b,action:'use_with',item_id:386,tool_id:8900};
  for(const points of [[a,b],[entry,b]]){
    const result=model.exports.selectTransition(points,[{from_index:0,id:'rope'}],edge,0);
    assert.deepEqual(result.points,points);assert.deepEqual(result.links,[{from_index:0,id:'rope'}]);
  }
});

test('an ordinary anchor on a hole is repaired in place and preserves later transitions',()=>{
  const entry=[10,10,7],origin=[10,11,7],exit=[10,10,8],edge={id:'dig',origin,entry,exit,action:'use_with_then_step'};
  const points=[[10,17,7],entry,exit,[9,10,8],[8,10,9]],links=[{from_index:3,id:'stairs'}];
  let fixed=model.exports.selectTransition(points,links,edge,0);
  assert.deepEqual(fixed.points,[points[0],origin,...points.slice(2)]);
  assert.deepEqual(fixed.links,[{from_index:1,id:'dig'},...links]);
  assert.deepEqual(fixed.sourceIndices,[0,1,2,3,4]);assert.deepEqual(points[1],entry,'source remains unchanged');
  fixed=model.exports.selectTransition([points[0],entry],[],edge);
  assert.deepEqual(fixed.points,[points[0],origin,exit]);assert.deepEqual(fixed.links,[{from_index:1,id:'dig'}]);
  fixed=model.exports.selectTransition([points[0],origin,entry,exit],[],edge,1);
  assert.deepEqual(fixed.points,[points[0],origin,exit]);assert.deepEqual(fixed.links,[{from_index:1,id:'dig'}]);
  const repeated=[points[0],entry,exit,points[0],entry,exit];
  assert.throws(()=>model.exports.selectTransition(repeated,[],edge),/kilka razy/);
  fixed=model.exports.selectTransition(repeated,[],edge,3);
  assert.deepEqual(fixed.points,[...repeated.slice(0,4),origin,exit]);assert.deepEqual(fixed.links,[{from_index:4,id:'dig'}]);
});

test('reconfirming a moved adjacent stair approach binds the existing floor change instead of appending',()=>{
  const edge={id:'stairs',origin:[10,11,8],entry:[11,10,8],exit:[11,10,9],action:'step'};
  const points=[[11,13,8],[11,11,8],edge.exit,[13,10,9]],links=[];
  const fixed=model.exports.selectTransition(points,links,edge,1);
  assert.deepEqual(fixed.points,points);assert.deepEqual(fixed.links,[{from_index:1,id:'stairs'}]);
  const tool=model.exports.selectTransition(points,links,{...edge,action:'use_with_then_step'},1);
  assert.deepEqual(tool.points,[points[0],edge.origin,points[2],points[3]],'complex tool sequence keeps the observed origin');
  const distant=[points[0],[20,20,8],edge.exit];
  assert.throws(()=>model.exports.selectTransition(distant,[],edge,1),/Nie dopisano/,'a repair must not append a distant transition to the end');
});

test('unmatched recorded uses identify the item and field instead of a generic route error',()=>{
  const points=[[10,10,7],[11,10,7]];
  const plan={path:points,complete:true,gaps:[],transitions:[],requires_action_review:true,requires_path_review:false,
    recorded_actions:[{item_id:408,x:10,y:10,z:7,matched_transition:null}]};
  assert.match(model.exports.simulationReport(plan,false,points).issues[0],/ID 408.*10, 10, 7/);
  assert.equal(model.exports.simulationReport({...plan,requires_action_review:false},false,points).ok,true);
  assert.equal(model.exports.simulationReport({...plan,requires_action_review:false,requires_path_review:true},false,points).ok,false);
});

test('stage visibility hides only the chosen visit and never changes recorded points',()=>{
  const host=hookHost();const path=[[100,100,7],[101,100,7],[101,100,6],[102,100,6],[101,100,7],[100,100,7]];
  const props={active:true,onRefresh:()=>{},state:{connected:true,passive_map:{center:[100,100,7],annotation_scope:'scope',cells:[]},cavebot:{recording:{active:true,observed_path:path.map(pos=>({pos}))},recordings:[]}}};
  const Panel=component('../app/AtlasPanel.tsx',{
    react:host.react,'@/lib/atlasModel':model.exports,'@/lib/atlasLoading':component('../lib/atlasLoading.ts'),
    '@/lib/auth':{authenticatedFetch:()=>{throw Error('visibility must not request or save');}},
    '@/lib/cavebotModel':component('../lib/cavebotModel.ts'),'@/lib/mapVisuals':{tileStyles:{},navigationStyles:{}},
    './PassiveMapPanel':{TileContextMenu:()=>null,NavigationDetails:()=>null},'./TransitionLearningPanel':{transitionActions:{}},
    './AtlasRouteOverview':{default:()=>null},'./MapTools':component('../app/MapTools.tsx'),'next/dynamic':{default:()=>()=>null},
  }).default;
  let nodes=elements(host.render(Panel,props));
  assert.ok(nodes.some(n=>n.props['aria-label']==='Odcinek 3 na 7'));
  nodes.find(n=>n.props['aria-label']==='Widoczność etapu 3').props.onChange();
  nodes=elements(host.render(Panel,props));
  assert.ok(!nodes.some(n=>n.props['aria-label']==='Odcinek 3 na 7'));
  assert.ok(nodes.some(n=>n.props['aria-label']==='Odcinek 1 na 7'));
  assert.equal(nodes.filter(n=>/^Widoczność etapu/.test(n.props['aria-label']??'')).length,3);
  nodes.find(n=>n.props.className==='show-all-stages').props.onClick();
  assert.ok(elements(host.render(Panel,props)).some(n=>n.props['aria-label']==='Odcinek 3 na 7'));
  assert.deepEqual(props.state.cavebot.recording.observed_path.map(r=>r.pos),path);
  assert.deepEqual(model.exports.transitionStages(path,[{origin:path[1],exit:path[2]},{origin:path[3],exit:path[4]}]),[{from:0,to:1},{from:1,to:2}]);
});

test('failed optimization shows its location and can save the original without accepting it',async()=>{
  const host=hookHost(),requests=[];let failSave=true;
  const props={active:true,onRefresh:()=>{},state:{connected:true,passive_map:{center:[100,100,7],annotation_scope:'scope',cells:[]},cavebot:{recording:{active:false},recordings:[]}}};
  const Panel=component('../app/AtlasPanel.tsx',{
    react:host.react,'@/lib/atlasModel':model.exports,'@/lib/atlasLoading':component('../lib/atlasLoading.ts'),
    '@/lib/auth':{authenticatedFetch:async(url,options)=>{
      const body=JSON.parse(options.body);requests.push({url,body});
      if(url.endsWith('/drafts'))return {ok:!failSave,json:async()=>failSave?{error:'Save failed'}:{saved_id:'saved',drafts:[{...body,id:'saved',updated:1}]}};
      const points=body.optimize?[body.points[0],body.points.at(-1)]:body.points;
      return {ok:true,json:async()=>({...body,points,original:body.points,path:points,complete:!body.optimize,links:[],transitions:[],loop_closed:false,
        gaps:body.optimize?[{segment:0,from:points[0],to:points[1],reason:'Pole docelowe jest zapisane jako zablokowane.'}]:[]})};
    }},
    '@/lib/cavebotModel':component('../lib/cavebotModel.ts'),'@/lib/mapVisuals':{tileStyles:{},navigationStyles:{}},
    './PassiveMapPanel':{TileContextMenu:()=>null,NavigationDetails:()=>null},'./TransitionLearningPanel':{transitionActions:{}},
    './AtlasRouteOverview':{default:()=>null},'./MapTools':component('../app/MapTools.tsx'),'next/dynamic':{default:()=>()=>null},
  }).default;
  const render=()=>elements(host.render(Panel,props));
  render().find(n=>n.props.label==='Edytuj waypointy').props.onClick();
  for(const x of [350,378,406]){
    const svg=render().find(n=>n.type==='svg');svg.props.ref.current={getBoundingClientRect:()=>({left:0,right:700,top:0,bottom:450,width:700,height:450})};
    svg.props.onPointerDown({button:0,ctrlKey:false,clientX:x,clientY:224});await new Promise(r=>setImmediate(r));
  }
  const original=requests.at(-1).body.points;
  await render().find(n=>n.type==='button'&&n.props.children==='Optymalizuj trasę').props.onClick();
  let nodes=render();assert.equal(nodes.find(n=>n.type==='button'&&n.props.children==='Przyjmij').props.disabled,true);
  assert.ok(nodes.some(n=>n.props.className==='atlas-gap'&&JSON.stringify(n.props.children).includes('zablokowane')));
  assert.ok(!JSON.stringify(nodes.filter(n=>n.type==='strong').map(n=>n.props.children)).includes('kroków'),'partial paths are not advertised as an improvement');
  nodes.find(n=>n.props.className==='atlas-gap').props.onClick();
  nodes=render();assert.equal(nodes.find(n=>n.props.label==='Śledź postać').props['aria-pressed'],false);
  assert.ok(!nodes.some(n=>n.props['aria-label']==='Porównanie optymalizacji'),'gap opens original in editable mode');
  assert.equal(nodes.find(n=>n.props.label==='Edytuj waypointy').props['aria-pressed'],true);
  assert.ok(nodes.filter(n=>/^Etykieta waypointu/.test(n.props['aria-label']??'')).every(n=>!n.props.disabled));
  // The original is still intact; editing never implicitly accepts the candidate.
  await nodes.find(n=>n.type==='button'&&n.props.children==='Optymalizuj trasę').props.onClick();
  const save=render().find(n=>n.props.className==='map-save-draft');assert.equal(save.props.disabled,false);assert.equal(save.props.children,'Zapisz oryginał');
  await save.props.onClick();assert.ok(render().some(n=>n.props['aria-label']==='Porównanie optymalizacji'),'failed save keeps preview and edits');
  failSave=false;await render().find(n=>n.props.className==='map-save-draft').props.onClick();
  assert.deepEqual(requests.at(-1).body.points,original);assert.equal(original.length,3);
  nodes=render();assert.ok(!nodes.some(n=>n.props['aria-label']==='Porównanie optymalizacji'));assert.equal(nodes.find(n=>n.props.className==='map-save-draft').props.disabled,true);
  await nodes.find(n=>n.type==='button'&&n.props.children==='Optymalizuj trasę').props.onClick();
  assert.deepEqual(requests.at(-1).body.points,original);
});

for(const mismatch of [false,true])test(`recorded-link repair preserves sketch edits and existing choices (geometry mismatch: ${mismatch})`,async()=>{
  const host=hookHost(),requests=[];let original=[];
  const props={active:true,onRefresh:()=>{},state:{connected:true,passive_map:{center:[100,100,7],annotation_scope:'scope',cells:[]},cavebot:{recording:{active:false},recordings:[{name:'source'}]}}};
  const Panel=component('../app/AtlasPanel.tsx',{
    react:host.react,'@/lib/atlasModel':model.exports,'@/lib/atlasLoading':component('../lib/atlasLoading.ts'),
    '@/lib/auth':{authenticatedFetch:async(url,options)=>{
      const body=JSON.parse(options.body);requests.push({url,body});
      if(url.endsWith('/drafts'))return {ok:true,json:async()=>({saved_id:'saved',drafts:[{...body,id:'saved',updated:1}]})};
      const points=body.recording?(mismatch?[...original,[150,150,7]]:original):body.points;
      const links=body.recording?[{from_index:0,id:'dig'},{from_index:1,id:'other'}]:points.length===3?[{from_index:1,id:'chosen'},...(body.links??[]).filter(l=>l.from_index===0)]:[];
      const complete=links.some(l=>l.from_index===0);
      return {ok:true,json:async()=>({...body,scope:'scope',points,path:points,original:points,links,complete,transitions:[],loop_closed:true,
        gaps:complete?[]:[{segment:0,from:points[0],to:points.at(-1),reason:'Missing transition'}]})};
    }},
    '@/lib/cavebotModel':component('../lib/cavebotModel.ts'),'@/lib/mapVisuals':{tileStyles:{},navigationStyles:{}},
    './PassiveMapPanel':{TileContextMenu:()=>null,NavigationDetails:()=>null},'./TransitionLearningPanel':{transitionActions:{}},
    './AtlasRouteOverview':{default:()=>null},'./MapTools':component('../app/MapTools.tsx'),'next/dynamic':{default:()=>()=>null},
  }).default;
  const render=()=>elements(host.render(Panel,props));
  render().find(n=>n.props.label==='Edytuj waypointy').props.onClick();
  for(const x of [350,378,406]){
    const svg=render().find(n=>n.type==='svg');svg.props.ref.current={getBoundingClientRect:()=>({left:0,right:700,top:0,bottom:450,width:700,height:450})};
    svg.props.onPointerDown({button:0,ctrlKey:false,clientX:x,clientY:224});await new Promise(r=>setImmediate(r));
  }
  original=requests.at(-1).body.points;
  render().find(n=>n.props['aria-label']==='Nazwa edytowanego szkicu').props.onChange({target:{value:'source'}});
  render().find(n=>n.props['aria-label']==='Etykieta waypointu 1').props.onChange({target:{value:'Start'}});
  await render().find(n=>n.props.className==='restore-recorded-transitions').props.onClick();
  let nodes=render();assert.equal(requests.filter(r=>r.url.endsWith('/drafts')).length,0,'repair must not save');
  assert.equal(nodes.find(n=>n.props['aria-label']==='Etykieta waypointu 1').props.value,'Start');
  if(mismatch)assert.ok(nodes.some(n=>n.props.role==='alert'&&String(n.props.children).includes('inny przebieg')));
  else assert.ok(!nodes.some(n=>n.props.className==='restore-recorded-transitions'));
  await nodes.find(n=>n.props.className==='map-save-draft').props.onClick();
  const saved=requests.at(-1).body;
  assert.deepEqual(saved.points,original);assert.equal(saved.name,'source');assert.equal(saved.labels[0],'Start');
  assert.deepEqual(saved.links.find(l=>l.from_index===1),{from_index:1,id:'chosen'});
  assert.equal(saved.links.some(l=>l.id==='dig'),!mismatch);
  if(!mismatch){
    render().find(n=>n.props.label==='Cofnij zmianę · Ctrl + Z').props.onClick();await new Promise(r=>setImmediate(r));
    assert.ok(render().some(n=>n.props.className==='restore-recorded-transitions'));
  }
  assert.ok(requests.every(r=>r.url.endsWith('/plan')||r.url.endsWith('/drafts')),'no gameplay endpoint');
});

test('dragging an anchor previews locally, plans once on drop, and cancels without losing the plan',async()=>{
  const host=hookHost(),requests=[];
  const original=[[99,100,7],[100,100,7],[101,100,7]];
  const Panel=component('../app/AtlasPanel.tsx',{
    react:host.react,'@/lib/atlasModel':model.exports,'@/lib/atlasLoading':component('../lib/atlasLoading.ts'),
    '@/lib/auth':{authenticatedFetch:async(url,options)=>{
      assert.ok(url.endsWith('/plan'));const body=JSON.parse(options.body);requests.push(body);
      const points=body.points??original;
      return {ok:true,json:async()=>({scope:'scope',original,points,links:[],path:points,complete:true,gaps:[],transitions:[]})};
    }},'@/lib/cavebotModel':component('../lib/cavebotModel.ts'),'@/lib/mapVisuals':{tileStyles:{},navigationStyles:{}},
    './PassiveMapPanel':{},'./TransitionLearningPanel':{transitionActions:{}},'./AtlasRouteOverview':{default:()=>null},
    './MapTools':component('../app/MapTools.tsx'),'next/dynamic':{default:()=>()=>null},
  }).default;
  const props={active:true,onRefresh:()=>{},state:{passive_map:{center:original[1],annotation_scope:'scope',cells:[]},cavebot:{recordings:[{name:'source'}]}}};
  const render=()=>elements(host.render(Panel,props));
  render().find(n=>n.props['aria-label']==='Nagranie do planowania').props.onChange({target:{value:'source'}});
  await render().find(n=>n.props.children==='Wczytaj nagranie').props.onClick();
  render().find(n=>n.props.label==='Edytuj waypointy').props.onClick();
  const svg=()=>render().find(n=>n.type==='svg').props;
  const target={getBoundingClientRect:()=>({left:0,right:700,top:0,bottom:450,width:700,height:450}),
    setPointerCapture(){},hasPointerCapture(){return true;},releasePointerCapture(){}};
  svg().ref.current=target;
  const event={button:0,clientX:350,clientY:226,pointerId:1,currentTarget:target};
  svg().onPointerDown(event);svg().onPointerMove({...event,clientX:351});svg().onPointerUp(event);
  assert.equal(requests.length,1,'click on an ordinary waypoint is a no-op');
  const moved={...event,clientY:255};
  svg().onPointerDown(event);svg().onPointerMove(moved);
  assert.ok(render().some(n=>n.props['aria-label']==='Podgląd przesuwanego waypointu'));
  assert.ok(render().some(n=>n.props.title==='Idź'&&n.props.children==='100, 100, 7'));
  svg().onPointerCancel();assert.equal(requests.length,1);
  svg().onPointerDown(event);svg().onPointerMove(moved);svg().onPointerUp(moved);
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.points),[undefined,[original[0],[100,101,7],original[2]]]);
  render().find(n=>n.props.label==='Cofnij zmianę · Ctrl + Z').props.onClick();
  assert.deepEqual(requests.at(-1).points,original);
});

test('change a staircase through its row, reject an unreachable exit, then preview and undo without saving',async()=>{
  const host=hookHost(),requests=[];
  const old={id:'south',origin:[1039,1300,4],entry:[1038,1301,4],exit:[1037,1301,3],action:'step'};
  const north={...old,id:'north',entry:[1038,1300,4],exit:[1037,1300,3]};
  const original=[[1041,1298,4],old.origin,old.exit,[1039,1299,3]];
  let blocked=true;
  const Panel=component('../app/AtlasPanel.tsx',{
    react:host.react,'@/lib/atlasModel':model.exports,'@/lib/atlasLoading':component('../lib/atlasLoading.ts'),
    '@/lib/auth':{authenticatedFetch:async(url,options)=>{
      assert.ok(url.endsWith('/plan'));const body=JSON.parse(options.body);requests.push(body);
      const points=body.points??original,links=body.links??[{from_index:1,id:'south'}];
      const current=links[0].id==='north'?north:old;
      const gaps=current===north && blocked?[{from:north.exit,to:original[3],segment:2,reason:'Brak dojścia'}]:[];
      return {ok:true,json:async()=>({scope:'scope',original,points,links,path:points,complete:!gaps.length,gaps,
        transitions:[current],legs:[{from_index:1,to_index:2,start:1,end:2}]})};
    }},'@/lib/cavebotModel':component('../lib/cavebotModel.ts'),'@/lib/mapVisuals':{tileStyles:{},navigationStyles:{}},
    './PassiveMapPanel':{},'./TransitionLearningPanel':{transitionActions:{}},'./AtlasRouteOverview':{default:()=>null},
    './MapTools':component('../app/MapTools.tsx'),'next/dynamic':{default:()=>()=>null},
  }).default;
  const props={active:true,onRefresh:()=>{},initialPosition:old.entry,state:{passive_map:{annotation_scope:'scope',cells:[]},cavebot:{recordings:[{name:'source'}]}}};
  const render=()=>elements(host.render(Panel,props));
  render().find(n=>n.props['aria-label']==='Nagranie do planowania').props.onChange({target:{value:'source'}});
  await render().find(n=>n.props.children==='Wczytaj nagranie').props.onClick();
  const picker=()=>render().find(n=>n.type?.name==='TransitionAlternatives');
  render().find(n=>n.props['aria-label']==='Zmień przejście T1').props.onClick();
  await picker().props.onChoose(north);
  assert.ok(render().some(n=>n.props.role==='alert'&&String(n.props.children).includes('Brak dojścia')));
  assert.equal(picker().props.current.id,'south');
  blocked=false;await picker().props.onChoose(north);
  assert.deepEqual(requests.at(-1).points,[original[0],old.origin,north.exit,original[3]]);
  assert.equal(picker(),undefined);
  render().find(n=>n.props.label==='Cofnij zmianę · Ctrl + Z').props.onClick();
  await new Promise(r=>setImmediate(r));assert.deepEqual(requests.at(-1).points,original);
  assert.deepEqual(requests.at(-1).links,[{from_index:1,id:'south'}]);
});

test('choosing a hole repairs the selected leg, preserves the viewport and opens the exit only on request',async()=>{
  const host=hookHost(),requests=[];
  const edge={id:'dig',origin:[100,101,7],entry:[100,100,7],exit:[100,100,8],action:'use_with_then_step',tool_id:3457,tool_ids:[3457,8900],prepared_item_id:594};
  const original=[[100,107,7],edge.entry,edge.exit,[101,100,8]];
  const props={active:true,onRefresh:()=>{},state:{connected:true,passive_map:{center:[100,100,7],annotation_scope:'scope',status:'ready',cells:[{...cell(...edge.entry),navigation:[{kind:'covered_hole'}]}]},cavebot:{recording:{active:false},recordings:[{name:'source'}]}}};
  const Panel=component('../app/AtlasPanel.tsx',{
    react:host.react,'@/lib/atlasModel':{...model.exports,transitionChoices:()=>[edge]},'@/lib/atlasLoading':component('../lib/atlasLoading.ts'),
    '@/lib/auth':{authenticatedFetch:async(url,options)=>{
      const body=JSON.parse(options.body);requests.push({url,body});const points=body.recording?original:body.points,complete=!!body.links?.length;
      return {ok:true,json:async()=>({...body,scope:'scope',points,original:points,path:complete?points:[points[0]],links:body.links??[],complete,transitions:complete?[edge]:[],loop_closed:false,gaps:complete?[]:[{segment:0,from:points[0],to:points[1],reason:'Missing entrance'}]})};
    }},'@/lib/cavebotModel':component('../lib/cavebotModel.ts'),'@/lib/mapVisuals':{tileStyles:{},navigationStyles:{covered_hole:{}}},
    './PassiveMapPanel':{TileContextMenu:()=>null,NavigationDetails:()=>null},'./TransitionLearningPanel':{transitionActions:{}},
    './AtlasRouteOverview':{default:()=>null},'./MapTools':component('../app/MapTools.tsx'),'next/dynamic':{default:()=>()=>null},
  }).default;
  const render=()=>elements(host.render(Panel,props));
  render().find(n=>n.props['aria-label']==='Nagranie do planowania').props.onChange({target:{value:'source'}});
  await render().find(n=>n.type==='button'&&n.props.children==='Wczytaj nagranie').props.onClick();
  render().find(n=>n.props['aria-label']==='Etykieta waypointu 4').props.onChange({target:{value:'After hole'}});
  render().find(n=>n.props.className==='atlas-route-gap').props.onClick();
  // Open the actual context menu callback at the known hole, then select its transition.
  let svg=render().find(n=>n.type==='svg');svg.props.ref.current={getBoundingClientRect:()=>({left:0,right:700,top:0,bottom:450,width:700,height:450})};
  // focusGap centers at (100,107,7); 7 cells north is the hole (25 columns).
  svg.props.onPointerDown({button:0,ctrlKey:false,clientX:350,clientY:28});
  let nodes=render();const picker=nodes.find(n=>n.props.className==='atlas-transition-picker');assert.ok(picker);
  const beforeBox=nodes.find(n=>n.type==='svg').props.viewBox;
  const choice=elements(picker).find(n=>n.props['data-transition-choice']==='dig');
  assert.match(JSON.stringify(choice.props.children),/Przypisz do odcinka 2 → 3/);
  await choice.props.onClick();
  assert.deepEqual(requests.at(-1).body.points,[original[0],edge.origin,edge.exit,original[3]]);
  assert.deepEqual(requests.at(-1).body.links,[{from_index:1,id:'dig'}]);
  nodes=render();assert.equal(nodes.find(n=>n.props['aria-label']==='Etykieta waypointu 4').props.value,'After hole');
  assert.equal(nodes.find(n=>n.props['aria-label']==='Piętro atlasu').props.value,7);
  assert.equal(nodes.find(n=>n.type==='svg').props.viewBox,beforeBox);
  assert.ok(nodes.some(n=>n.type==='summary'&&JSON.stringify(n.props.children).includes('100, 100, 7')),'inspector stays on the entry');
  const toolbar=nodes.find(n=>n.props['aria-label']==='Narzędzia mapy');assert.ok(elements(toolbar).some(n=>n.type==='strong'&&n.props.children==='(100, 107, 7)'),'center stays unchanged');
  nodes.find(n=>n.type==='button'&&Array.isArray(n.props.children)&&n.props.children[0]==='Pokaż wyjście · ').props.onClick();
  nodes=render();assert.equal(nodes.find(n=>n.props['aria-label']==='Piętro atlasu').props.value,8);
  assert.ok(!nodes.some(n=>n.props.className==='atlas-route-gap'));
  nodes.find(n=>n.props.label==='Cofnij zmianę · Ctrl + Z').props.onClick();await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.at(-1).body.points,original);assert.deepEqual(requests.at(-1).body.links,[]);
  assert.ok(requests.every(r=>r.url.endsWith('/plan')),'only planning, no save or gameplay requests');
});

test('far atlas zoom covers the entire viewport with bounded queries and merges cached chunks',async()=>{
  const {atlasRegionQueries,createAtlasRegionReader}=component('../lib/atlasLoading.ts');
  const center=[1000,1000,8],grid=model.exports.atlasViewport(1280,700,96),queries=atlasRegionQueries(center,grid.columns,grid.rows);
  assert.ok(grid.visibleRadius>32&&queries.length>1&&queries.length<=16);
  for(const x of [center[0]-grid.offsetX,center[0]-grid.offsetX+Math.floor(grid.columns)-1])for(const y of [center[1]-grid.offsetY,center[1]-grid.offsetY+Math.floor(grid.rows)-1])
    assert.ok(queries.some(q=>Math.abs(q.center[0]-x)<=q.radius&&Math.abs(q.center[1]-y)<=q.radius));
  let count=0,concurrent=0,maximum=0,time=0,scope='one';
  const reader=createAtlasRegionReader(async(p,r)=>{count++;concurrent++;maximum=Math.max(maximum,concurrent);await new Promise(r=>setImmediate(r));concurrent--;
    return {scope,center:p,radius:r,cells:[{x:p[0],y:p[1],z:p[2]},{x:1000,y:1000,z:8,seen_at:count}],total:123,limit:null,floors:[8],route_transitions:[]};
  },()=>time);
  let data=await reader(center,grid.columns,grid.rows,scope,()=>false);
  assert.equal(data.cells.length,queries.length+1);assert.equal(data.total,123);assert.equal(maximum,3);assert.ok(data.radius>32);
  const first=count;await reader(center,grid.columns,grid.rows,scope,()=>false);assert.equal(count,first,'stable chunks are cached');
  time=10001;await reader(center,grid.columns,grid.rows,scope,()=>false);assert.equal(count,first*2,'cache refreshes');
  scope='two';await reader(center,grid.columns,grid.rows,scope,()=>false);assert.equal(count,first*3,'profile change clears old chunks');
  const close=count;await reader(center,grid.columns,grid.rows,scope,()=>true);assert.equal(count,close,'closed view schedules no work');
  await reader(center,25,15,scope,()=>false);await reader(center,25,15,scope,()=>false);assert.equal(count,close+2,'near view remains live, without the wide-view cache delay');
  assert.ok(atlasRegionQueries([0,0,0],193,100).every(q=>q.center.every(n=>n>=0)&&q.radius<=32));
});

test('wide map reads stop after cancellation and never mix profiles',async()=>{
  const {createAtlasRegionReader}=component('../lib/atlasLoading.ts');let cancelled=false,calls=0;
  const read=createAtlasRegionReader(async p=>{calls++;cancelled=true;return {scope:'x',center:p,radius:32,cells:[],floors:[7],total:1,limit:null};});
  assert.equal(await read([100,100,7],193,150,'x',()=>cancelled),undefined);assert.equal(calls,3);
  const wrong=createAtlasRegionReader(async p=>({scope:'other',center:p,radius:32,cells:[],floors:[7],total:1,limit:null}));
  await assert.rejects(()=>wrong([100,100,7],193,150,'expected',()=>false),/Profil atlasu/);
});

test('saving combat independently does not invalidate sketch edits, but concurrent geometry changes do',async()=>{
  const host=hookHost(),requests=[];let savedRow;
  const props={active:true,onRefresh:()=>{},state:{connected:true,passive_map:{center:[100,100,7],annotation_scope:'scope',cells:[]},cavebot:{recording:{active:false},recordings:[]}}};
  const Panel=component('../app/AtlasPanel.tsx',{
    react:host.react,'@/lib/atlasModel':model.exports,'@/lib/atlasLoading':component('../lib/atlasLoading.ts'),
    '@/lib/auth':{authenticatedFetch:async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;requests.push({url,body});
      if(url.endsWith('/drafts')){
        if(body)savedRow={...body,id:'saved',navigation_revision:'saved-shape',updated:(savedRow?.updated??0)+1};
        return {ok:true,json:async()=>({scope:'scope',saved_id:'saved',drafts:[savedRow]})};
      }
      return {ok:true,json:async()=>({...body,path:body.points,original:body.points,complete:true,transitions:[],gaps:[],loop_closed:true})};
    }},'@/lib/cavebotModel':component('../lib/cavebotModel.ts'),'@/lib/mapVisuals':{tileStyles:{},navigationStyles:{}},
    './PassiveMapPanel':{TileContextMenu:()=>null,NavigationDetails:()=>null},'./TransitionLearningPanel':{transitionActions:{}},
    './AtlasRouteOverview':{default:()=>null},'./MapTools':component('../app/MapTools.tsx'),'next/dynamic':{default:()=>()=>null},
  }).default;
  const render=()=>elements(host.render(Panel,props));render().find(n=>n.props.label==='Edytuj waypointy').props.onClick();
  for(const x of [350,378]){const svg=render().find(n=>n.type==='svg');svg.props.ref.current={getBoundingClientRect:()=>({left:0,right:700,top:0,bottom:450,width:700,height:450})};svg.props.onPointerDown({button:0,ctrlKey:false,clientX:x,clientY:224});await new Promise(r=>setImmediate(r));}
  await render().find(n=>n.props.className==='map-save-draft').props.onClick();
  render().find(n=>n.props['aria-label']==='Piętro atlasu').props.onChange({target:{value:'8'}});
  for(let i=0;i<20;i++)render().find(n=>n.props.label==='Oddal mapę · Shift + scroll').props.onClick();
  const viewportBeforeReload=render().find(n=>n.type==='svg').props.viewBox;
  assert.ok(Number(viewportBeforeReload.split(' ')[2])>65,'zoom extends beyond the old 65-tile view');
  render().find(n=>n.props['aria-label']==='Zapisany szkic').props.onChange({target:{value:'saved'}});
  await new Promise(r=>setImmediate(r));
  assert.equal(render().find(n=>n.props['aria-label']==='Piętro atlasu').props.value,8,'same-route refresh after Save preserves floor');
  assert.equal(render().find(n=>n.type==='svg').props.viewBox,viewportBeforeReload,'same-route refresh preserves zoom');
  render().find(n=>n.props['aria-label']==='Etykieta waypointu 1').props.onChange({target:{value:'My edit'}});
  savedRow={...savedRow,updated:10,combat:{enabled:true}};
  await render().find(n=>n.props.className==='map-save-draft').props.onClick();
  assert.equal(requests.at(-1).body.expected_updated,10);assert.equal(savedRow.labels[0],'My edit');
  props.state.adaptive_cavebot={status:'running',draft_id:'saved',waypoint:{navigation_revision:'previous-shape'}};
  assert.ok(render().some(n=>n.props.role==='status'&&n.props.children==='Zapisano na następny start'));
  props.state.adaptive_cavebot.waypoint.navigation_revision='saved-shape';
  assert.ok(!render().some(n=>n.props.children==='Zapisano na następny start'));
  render().find(n=>n.props['aria-label']==='Etykieta waypointu 1').props.onChange({target:{value:'Keep locally'}});
  savedRow={...savedRow,updated:20,points:[[200,200,7],[201,200,7]]};
  const posts=requests.filter(r=>r.url.endsWith('/drafts')&&r.body).length;
  await render().find(n=>n.props.className==='map-save-draft').props.onClick();
  assert.equal(requests.filter(r=>r.url.endsWith('/drafts')&&r.body).length,posts);
  assert.equal(render().find(n=>n.props['aria-label']==='Etykieta waypointu 1').props.value,'Keep locally');
  assert.ok(render().some(n=>n.props.role==='alert'&&String(n.props.children).includes('innym oknie')));
});

test('loop preference does not mark geometry dirty or erase existing edits',()=>{
  const {draftFingerprint,hasDraftChanges}=model.exports;
  const points=[[10,10,7],[13,10,7]],labels=['Start','End'],links=[];
  const baseline=draftFingerprint('Route',points,labels,links,false);
  assert.equal(hasDraftChanges('id',baseline,'Route',points,labels,links,true),false);
  assert.equal(hasDraftChanges('id',baseline,'Route',[[10,10,7],[14,10,7]],labels,links,true),true);
});

test('loop button persists only the execution preference and preserves unsaved labels on failure and success',async()=>{
  const host=hookHost(),requests=[];let savedRow,fail=false;
  const changed=[];
  const props={active:true,onRefresh:()=>{},onLoopChange:v=>changed.push(v),state:{connected:true,passive_map:{center:[100,100,7],annotation_scope:'scope',cells:[]},cavebot:{recording:{active:false},recordings:[]}}};
  const Panel=component('../app/AtlasPanel.tsx',{
    react:host.react,'@/lib/atlasModel':model.exports,'@/lib/atlasLoading':component('../lib/atlasLoading.ts'),
    '@/lib/auth':{authenticatedFetch:async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;requests.push({url,body});
      if(url.endsWith('/drafts')){
        if(body?.operation==='set_loop'){
          if(fail)return {ok:false,json:async()=>({error:'Test failure'})};
          savedRow={...savedRow,loop:body.loop,updated:2};
        }else if(body)savedRow={...body,id:'saved',updated:1};
        return {ok:true,json:async()=>({scope:'scope',saved_id:'saved',drafts:[savedRow]})};
      }
      return {ok:true,json:async()=>({...body,path:body.points,original:body.points,complete:true,transitions:[],gaps:[],loop_closed:true})};
    }},'@/lib/cavebotModel':component('../lib/cavebotModel.ts'),'@/lib/mapVisuals':{tileStyles:{},navigationStyles:{}},
    './PassiveMapPanel':{TileContextMenu:()=>null,NavigationDetails:()=>null},'./TransitionLearningPanel':{transitionActions:{}},
    './AtlasRouteOverview':{default:()=>null},'./MapTools':component('../app/MapTools.tsx'),'next/dynamic':{default:()=>()=>null},
  }).default;
  const render=()=>elements(host.render(Panel,props));const flush=()=>new Promise(r=>setImmediate(r));
  render().find(n=>n.props.label==='Edytuj waypointy').props.onClick();
  for(const x of [350,378]){const svg=render().find(n=>n.type==='svg');svg.props.ref.current={getBoundingClientRect:()=>({left:0,right:700,top:0,bottom:450,width:700,height:450})};svg.props.onPointerDown({button:0,ctrlKey:false,clientX:x,clientY:224});await flush();}
  await render().find(n=>n.props.className==='map-save-draft').props.onClick();
  requests.length=0;
  render().find(n=>n.props.icon==='loop').props.onClick();await flush();
  assert.deepEqual(requests.find(r=>r.url.endsWith('/drafts')).body,{scope:'scope',id:'saved',operation:'set_loop',loop:false});
  assert.equal(render().find(n=>n.props.className==='map-save-draft').props.disabled,true,'loop does not dirty sketch');
  render().find(n=>n.props['aria-label']==='Etykieta waypointu 1').props.onChange({target:{value:'Unsaved edit'}});
  fail=true;render().find(n=>n.props.icon==='loop').props.onClick();await flush();
  assert.equal(render().find(n=>n.props.icon==='loop').props['aria-pressed'],false);
  assert.equal(render().find(n=>n.props['aria-label']==='Etykieta waypointu 1').props.value,'Unsaved edit');
  fail=false;render().find(n=>n.props.icon==='loop').props.onClick();await flush();
  assert.equal(render().find(n=>n.props.icon==='loop').props['aria-pressed'],true);
  assert.equal(render().find(n=>n.props['aria-label']==='Etykieta waypointu 1').props.value,'Unsaved edit');
  assert.equal(savedRow.labels[0],'','loop change did not save geometry edits');
  assert.deepEqual(changed,[false,true]);
});
