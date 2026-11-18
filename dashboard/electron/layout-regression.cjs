/* Offscreen layout regression: creates only an invisible test renderer.
 * No production URL, session credential, game connection or OS input.
 * Usage: electron electron/layout-regression.cjs <artifact-directory>
 */
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const ts=require('typescript'),React=require('react'),render=require('react-dom/server').renderToStaticMarkup;
const root=path.resolve(__dirname,'..'),out=path.resolve(process.argv[2]);
function component(file,imports={}) {
  const mod={exports:{}};
  new Function('exports','require',ts.transpileModule(fs.readFileSync(path.join(root,file),'utf8'),
    {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText)
    (mod.exports,spec=>imports[spec]??require(spec));
  return mod.exports;
}
function descendants(node){
  if(Array.isArray(node))return node.flatMap(descendants);
  if(!node?.props)return [];
  return [node,...descendants(node.props.children)];
}
async function fixture(width,height){
  const values=[];let cursor=0;
  const hooks={...React,useEffect:()=>{},useLayoutEffect:()=>{},useRef:v=>{const i=cursor++;if(!(i in values))values[i]={current:v};return values[i];},useState:v=>{const i=cursor++;if(!(i in values))values[i]=v?.width===700&&v?.height===450?{width:width-40,height:height-232}:typeof v==='function'?v():v;return [values[i],next=>{values[i]=typeof next==='function'?next(values[i]):next;}];}};
  const display=process.argv.includes('--route-display');
  const route=display?[[103,101,7],[103,100,8],[102,100,8],[101,100,8],[100,100,8],[100,99,8],[101,98,9],[102,98,9],[103,98,8],[103,99,8],[103,100,7],[103,101,7],[103,100,8]]:[[97,103,7],[98,103,7],[99,102,7],[99,101,7],[99,100,7],[99,99,7],[99,99,6],[100,99,6],[101,99,6],[102,99,6],[103,99,6],[103,98,7],[103,99,7],[104,100,7],[104,101,7],[103,102,7],[102,103,7],[101,103,7],[100,103,7],[99,103,7],[98,103,7],[97,103,7]];
  const anchors=[route[0],route[3],route[5],route[6],route[10],route[11],route[14],route[17],route.at(-1)];
  const plan={points:anchors,original:route,path:route,complete:true,loop_closed:true,gaps:[],links:[],transitions:[{origin:route[5],entry:[99,98,7],exit:route[6],action:'use'},{origin:route[10],entry:[103,98,6],exit:route[11],action:'step'}]};
  if(display){plan.points=route;plan.avoid_tiles=[[100,100,8]];plan.requires_action_review=true;plan.transitions=[
    {origin:route[0],entry:[103,100,7],exit:route[1],action:'use_with_then_step',tool_id:3457,item_id:593,prepared_item_id:594},
    {origin:route[5],entry:[101,98,8],exit:route[6],action:'step'},
    {origin:route[7],entry:route[7],exit:route[8],action:'use_with',tool_id:8900},
    {origin:route[9],entry:route[9],exit:route[10],action:'use'},
    {origin:route[11],entry:[103,100,7],exit:route[12],action:'use_with_then_step',tool_id:3457,item_id:593,prepared_item_id:594},
  ];}
  const Panel=component('app/AtlasPanel.tsx',{
    react:hooks,'react-dom':{createPortal:n=>n},'next/dynamic':{default:()=>()=>null},
    '@/lib/atlasModel':component('lib/atlasModel.ts'),'@/lib/atlasLoading':component('lib/atlasLoading.ts'),
    '@/lib/auth':{authenticatedFetch:async url=>{assert.ok(url.endsWith('/api/map/plan')||url.endsWith('/api/cavebot/record/stop'));return {ok:true,json:async()=>url.endsWith('/plan')?plan:{}};}},
    '@/lib/cavebotModel':component('lib/cavebotModel.ts'),'@/lib/mapVisuals':component('lib/mapVisuals.ts'),
    './PassiveMapPanel':{TileContextMenu:()=>null,NavigationDetails:()=>null},'./TransitionLearningPanel':{transitionActions:{}},
    './AtlasRouteOverview':{default:()=>null},'./MapTools':component('app/MapTools.tsx'),
  }).default;
  const cells=[];
  for(let x=88;x<=112;x++)for(let y=88;y<=112;y++)cells.push({x,y,z:display?8:7,kind:x===90||y===90?'blocked':'clear',items:[],stairs:null,occupants:0});
  for(const [x,y,kind] of display?[[101,98,'passage'],[103,100,'ladder']]:[[99,98,'ladder'],[103,98,'hole'],[101,102,'door']]){
    const c=cells.find(c=>c.x===x&&c.y===y);c.navigation=[{kind,source:'observed',door_state_hint:kind==='door'?'closed':'unknown'}];
  }
  const props={active:true,onRefresh:()=>{props.state.cavebot.recording.active=false;},state:{connected:true,passive_map:{center:[100,100,display?8:7],annotation_scope:'fixture',status:'ready',stale:false,cells},cavebot:{recording:{active:true,name:'Przykładowa trasa',observed_path:route.map(pos=>({pos}))},recordings:[]}}};
  const draw=()=>{cursor=0;return Panel(props);};
  await descendants(draw()).find(n=>n.props.label==='Zakończ nagranie').props.onClick();
  if(!display)descendants(draw()).find(n=>n.props.label==='Edytuj waypointy').props.onClick();
  if(process.argv.includes('--transition')){
    const canvas=descendants(draw()).find(n=>n.type==='svg');
    const widthPx=width-40,heightPx=height-232,grid=component('lib/atlasModel.ts').atlasViewport(widthPx,heightPx,12);
    canvas.props.ref.current={getBoundingClientRect:()=>({left:0,top:0,right:widthPx,bottom:heightPx,width:widthPx,height:heightPx})};
    canvas.props.onContextMenu({preventDefault:()=>{},clientX:(99-100+grid.offsetX+.5)/grid.columns*widthPx,clientY:(98-100+grid.offsetY+.5)/grid.rows*heightPx});
    const menu=descendants(draw()).find(n=>n.props.onTransition);assert.ok(menu);
    menu.props.onTransition();menu.props.onClose();
  }
  const tree=draw();
  if(display){const nodes=descendants(tree);assert.ok(nodes.some(n=>n.props['aria-label']==='Dojście do T2'));assert.ok(nodes.some(n=>n.props['aria-label']==='T1/T5: przyjście z piętro 7'));assert.ok(nodes.some(n=>n.props.className==='atlas-execution-issues'));}
  const surface=descendants(tree).find(n=>n.props.className==='atlas-editing-surface');assert.ok(surface);
  return render(React.createElement('div',{className:'route-map-overlay'},React.createElement('header',null,'Szkic — test układu'),surface));
}
app.disableHardwareAcceleration();
app.on('window-all-closed',()=>{}); // Keep the test alive between viewport cases.
app.whenReady().then(async()=>{
  fs.mkdirSync(out,{recursive:true});
  const cssDir=path.join(root,'.next/static/css');
  const css=fs.readdirSync(cssDir).filter(n=>n.endsWith('.css')).map(n=>fs.readFileSync(path.join(cssDir,n),'utf8')).join('\n');
  const html=path.join(out,'layout-fixture.html');
  const results=[];
  for(const [width,height] of [[1280,900],[2491,1288]]){
    fs.writeFileSync(html,`<!doctype html><html lang="pl"><meta charset="utf-8"><style>${css}</style><body>${await fixture(width,height)}</body></html>`);
    const win=new BrowserWindow({width,height,show:false,webPreferences:{offscreen:true,contextIsolation:true,sandbox:true}});
    await win.loadURL('data:text/html;charset=utf-8,'+encodeURIComponent(fs.readFileSync(html,'utf8')));
    const result=await win.webContents.executeJavaScript(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>{
      const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
      resolve({viewport:{width:innerWidth,height:innerHeight},controls:rect('.map-draft-controls'),input:rect('.map-draft-controls input'),map:rect('.atlas-canvas'),picker:document.querySelector('.atlas-transition-picker')?rect('.atlas-transition-picker'):null,direction:getComputedStyle(document.querySelector('.map-draft-controls')).flexDirection});
    })))`);
    assert.equal(result.direction,'row');assert.ok(result.controls.height<85,JSON.stringify(result));
    assert.ok(result.input.height<40,JSON.stringify(result));assert.ok(result.map.height>result.viewport.height*.55,JSON.stringify(result));
    if(process.argv.includes('--transition'))assert.ok(result.picker&&result.picker.x>=0&&result.picker.y>=0&&result.picker.x+result.picker.width<=width&&result.picker.y+result.picker.height<=height,JSON.stringify(result));
    fs.writeFileSync(path.join(out,`layout-${width}.png`),(await win.webContents.capturePage()).toPNG());
    results.push(result);win.destroy();
  }
  fs.writeFileSync(path.join(out,'layout-results.json'),JSON.stringify(results,null,2));
  console.log(JSON.stringify({passed:true,results}));app.quit();
}).catch(e=>{console.error(e.stack);app.exit(1);});
