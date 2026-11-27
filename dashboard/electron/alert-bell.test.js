const test=require('node:test'),assert=require('node:assert/strict');
const config={private:{enabled:true,sound:true,scope:'always'},default:{enabled:true,sound:true,scope:'always'},player:{enabled:true,sound:true,scope:'cavebot'}};
const snapshot=(events=[],extra={})=>({session:'one',latest_id:events.at(-1)?.id??0,events,config,cavebot_active:true,...extra});
const event=(id,extra={})=>({id,kind:'private',at:100,sound:true,...extra});
test('an unread preview stays dismissible even after four seconds; a read alert has no preview',()=>{
 const fs=require('node:fs'),ts=require('typescript'),React=require('react');
 const {renderToStaticMarkup}=require('react-dom/server');
 const source=fs.readFileSync(require.resolve('../app/AlertBell.tsx'),'utf8');
 const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,
   target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const mod={exports:{}};
 new Function('exports','require',js)(mod.exports,name=>name==='@/lib/api'?{}:require(name));
 const render=(age,unread=1,paused=false,automation_paused=false)=>renderToStaticMarkup(React.createElement(mod.exports.default,{
   connected:true,onRefresh(){},alerts:snapshot([event(1,{at:Date.now()/1000-age,sender:'Gracz',text:'Test'})],{unread,paused,automation_paused})}));
 assert.doesNotMatch(render(0,1,true),/class="alert-peek"|class="alert-count"/);
 assert.match(render(0,1,true),/aria-label="Wznów alerty"/);
 const globalPause=render(0,1,true,true);
 assert.match(globalPause,/aria-label="Alerty wstrzymane przez pauzę automatyki"[^>]*disabled/);
 assert.doesNotMatch(globalPause,/class="alert-peek"|class="alert-count"/);
 assert.match(render(0),/class="alert-peek"/);
 const after=render(5);
 assert.match(after,/class="alert-peek"/);
 assert.match(after,/aria-label="Zamknij podgląd alertu"/);
 assert.doesNotMatch(render(5,0),/class="alert-peek"/);
 assert.match(after,/class="alert-count">1</);
});
