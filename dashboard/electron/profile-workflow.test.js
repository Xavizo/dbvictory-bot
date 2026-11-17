const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript');
const compile=file=>ts.transpileModule(fs.readFileSync(require.resolve(file),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;

function workspace(){
 const slots=[],deps=[],effects=[],calls=[];let cursor=0;
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;}];},useRef(initial){const i=cursor++;return slots[i]??=( {current:initial});},useEffect(fn,next){const i=cursor++;if(!deps[i]||next.some((v,j)=>v!==deps[i][j])){deps[i]=next;effects.push(fn);}}};
 const model={};new Function('exports',compile('../lib/cavebotModel.ts'))(model);
 const combat={...model.defaultCombat(),enabled:true,rules:[{pattern:'Wolf',action:'attack',priority:50}]};
 const routes=['one','two'].map(id=>({id,name:id,points:[[10,10,7],[12,10,7]],labels:[],links:[],loop:true,updated:1,combat}));
 const stub=()=>null;
 const imports={react,'@/lib/cavebotModel':model,'@/lib/auth':{authenticatedFetch:async(url,init)=>{const body=init&&JSON.parse(init.body);calls.push({url,body});return {ok:true,json:async()=>url.endsWith('/drafts')?{drafts:routes,scope:'scope'}:{message:{config:combat}}};}}};
 const exported={};
 new Function('exports','require','window','setInterval','clearInterval',compile('../app/CavebotWorkspace.tsx'))(exported,name=>imports[name]??(name==='react/jsx-runtime'?require(name):{default:stub}),{addEventListener(){},removeEventListener(){}},()=>1,()=>{});
 let state={connected:true,actions:[],combat_profile:combat,profile_selection:{routes:{'scope:two':'bound'}},adaptive_cavebot:{status:'idle'},cavebot:{recording:{active:false}}};
 function render(){cursor=0;return exported.default({state,active:true,onRefresh:async()=>{}});}
 function nodes(root){if(!root||typeof root!=='object')return [];return [root,...Object.values(root.props??{}).flat(Infinity).flatMap(nodes)];}
 const find=(root,p)=>nodes(root).find(p);
 async function flush(){for(let i=0;i<4;i++){render();while(effects.length)effects.shift()();await new Promise(r=>setImmediate(r));}return render();}
 return {render,flush,find,calls,combat};
}
test('imported combat edits leave draft tools unlocked and survive selecting a bound route',async()=>{
 const w=workspace();let root=await w.flush();
 w.find(root,n=>n.props?.['aria-label']==='Wybrana trasa').props.onChange({target:{value:'one'}});root=await w.flush();
 const profile=w.find(root,n=>n.props?.kind==='combat');
 profile.props.onImport({...w.combat,lure_count:3});root=await w.flush();
 assert.equal(w.find(root,n=>n.props?.['aria-label']==='Wybrana trasa').props.disabled,false);
 const atlas=w.find(root,n=>n.props?.initialDraftId==='one');assert.ok(atlas);
 for(const button of [atlas.props.moreActions.props.children].flat(Infinity).filter(n=>n?.type==='button'))assert.equal(button.props.disabled,false);
 assert.equal(w.find(root,n=>n.props?.kind==='combat').props.dirty,true);
 w.find(root,n=>n.props?.['aria-label']==='Wybrana trasa').props.onChange({target:{value:'two'}});root=await w.flush();
 assert.equal(w.find(root,n=>n.props?.kind==='combat').props.config.lure_count,3);
 assert.ok(w.find(root,n=>n.props?.initialDraftId==='two'));
 assert.equal(w.calls.filter(c=>c.body?.command==='apply').length,0);
 assert.equal(w.calls.filter(c=>c.body?.command==='start').length,0);
});
test('unsaved geometry still protects switching route independently of combat',async()=>{
 const w=workspace();let root=await w.flush();
 const atlas=w.find(root,n=>n.props?.embedded===true);atlas.props.onEditingChange(true);root=await w.flush();
 assert.equal(w.find(root,n=>n.props?.['aria-label']==='Wybrana trasa').props.disabled,true);
 assert.equal(w.calls.filter(c=>c.body).length,0);
});
