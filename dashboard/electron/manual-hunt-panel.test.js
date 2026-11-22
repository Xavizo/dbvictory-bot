const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');

test('a discovered ground without a draft previews a loop and saves only on request', async () => {
  const values = [], deps = [], effects = [], posts = [];
  let cursor = 0;
  const react = {
    useState(initial) {
      const i = cursor++;
      if (!(i in values)) values[i] = typeof initial === 'function' ? initial() : initial;
      return [values[i], v => { values[i] = typeof v === 'function' ? v(values[i]) : v; }];
    },
    useRef(initial) { return values[cursor++] ??= {current: initial}; },
    useMemo(fn) { return fn(); },
    useEffect(fn, next) {
      const i = cursor++;
      if (!deps[i] || next.some((v,j) => v !== deps[i][j])) effects.push(fn);
      deps[i] = next;
    },
  };
  const area = {id:'manual:abc',name:'Brolly',visits:2,xp:900,cells:[[24,32,7],[32,32,7],[40,32,7],[32,32,8]],drafts:[],
    eligible:true,sightings:[],needs_recheck:true};
  const via=[[24,24,8]];
  const passage={id:'down',entry:[26,31,7],origin:[26,32,7],exit:[26,31,8],action:'use'};
  const points = [[25,33,7],[26,33,7],[25,33,7]];
  const auth = {authenticatedFetch: async (url, init) => {
    const body = init.body ? JSON.parse(init.body) : undefined;
    if (body) posts.push(body);
    let result;
    if (url.includes('/discoveries?')) result = {scope:'test',discoveries:[{...area,id:'manual:other',cells:[[24,32,6]]},area],revision:'abc'};
    else if (url.endsWith('/grounds')) result = {scope:'test',sector_size:8,areas:[{id:'far',name:'Daleko',cells:[[80,80,7]],drafts:[]}],drafts:[]};
    else if (url.endsWith('/route')) result = {points,path:points,links:[],entrance:points[0],total:body.selected.length,covered:body.selected.length,missing:[],selected:body.selected,transitions:0,sightings:0,via:body.via??[],
      ...(body.selected.length===area.cells.length&&!body.via?{covered:3,missing:[area.cells[3]],connector:{sectors:via,covered:4,passages:[passage]}}:{})};
    else if (url.endsWith('/drafts')) result = {saved_id:'new-id',drafts:[{id:'new-id',name:body.name,terrain:body.terrain}]};
    else throw Error(url);
    return {ok:true,json:async () => result};
  }};
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(require.resolve('../app/HuntAreas.tsx'),'utf8'),
    {compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
  const spawnModel={};
  new Function('exports',ts.transpileModule(fs.readFileSync(require.resolve('../lib/spawnAreas.ts'),'utf8'),
    {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(spawnModel);
  const wildlife={};
  new Function('exports',ts.transpileModule(fs.readFileSync(require.resolve('../lib/atlasWildlife.ts'),'utf8'),
    {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(wildlife);
  const imports = {react,'@/lib/auth':auth,'@/lib/spawnAreas':spawnModel,'@/lib/atlasWildlife':wildlife,'./WorldMap':{default:()=>null}};
  new Function('exports','require','document','setInterval','clearInterval',code)(
    exports, name => imports[name] ?? require(name), {hidden:false}, ()=>1, ()=>{});
  function nodes(node) {
    if (!node || typeof node !== 'object') return [];
    return [node,...[node.props?.children].flat(Infinity).flatMap(nodes)];
  }
  let location=null;
  async function render() {
    let root;
    for (let i=0;i<5;i++) {
      cursor = 0;
      root = exports.default({initialFloor:7,location,onOpen:()=>{},onClose:()=>{}});
      while (effects.length) effects.shift()();
      await new Promise(done => setImmediate(done));
    }
    return nodes(root);
  }
  let panel = await render();
  const current=()=>panel.find(n=>n.props['aria-label']==='Obszar lub szkic').props.value;
  assert.equal(current(),'');
  location=[26,33,7];panel=await render();
  assert.equal(current(),area.id);
  assert.ok(panel.some(n => n.props.children === 'Możesz przeliczyć pętlę z zapisanej wizyty.'));
  assert.deepEqual(posts, []);
  const button = label => panel.find(n => n.type === 'button' && n.props.children === label);
  const ground=()=>panel.find(n=>n.props.ground).props.ground;
  const drag=async(a,b)=>{ground().selectSectors(a,b);panel=await render();};
  const click=async(label)=>{await button(label).props.onClick();panel=await render();};
  assert.equal(button('Zaproponuj pętlę').props.disabled,true);
  await click('Wybierz teren');
  await drag([46,35,7],[32,32,7]);
  await drag([32,32,8],[32,32,8]);
  const selected=area.cells.slice(1);
  assert.deepEqual(ground().selected,selected);
  assert.deepEqual(posts,[]);
  await click('Zaproponuj pętlę');
  assert.deepEqual(posts,[{scope:'test',ground:area.id,selected}]);
  assert.ok(panel.some(n=>n.props.children==='Propozycja pętli gotowa'));
  await click('Zapisz jako nowy szkic');
  assert.deepEqual(posts[1].points,points);
  assert.deepEqual(posts[1].terrain,{ground:area.id,sectors:selected});
  const choose=panel.find(n=>n.props['aria-label']==='Obszar lub szkic');
  choose.props.onChange({target:{value:'draft:new-id'}});panel=await render();
  location=[80,80,7];panel=await render();
  assert.equal(current(),'draft:new-id');
  assert.deepEqual(ground().selected,selected);
  assert.deepEqual(ground().cells,area.cells);
  await click('Zaproponuj pętlę');
  assert.equal(posts.at(-1).ground,area.id);
  await click('Wybierz teren');
  await drag([32,32,7],[47,35,7]);
  assert.deepEqual(ground().selected,[[32,32,8]]);
  assert.equal(ground().route,undefined);
  assert.equal(button('Zapisz jako nowy szkic'),undefined);
  await click('Cofnij wybór');
  assert.deepEqual(ground().selected,selected);
  assert.deepEqual(ground().route,points);
  await click('Całe expowisko');
  assert.deepEqual(ground().selected,area.cells);
  await click('Przelicz pętlę');
  assert.deepEqual(ground().connector.sectors,via);
  assert.deepEqual(ground().via,[]);
  const before=posts.length;
  const floorButton=panel.find(n=>n.type==='button'&&Array.isArray(n.props.children)&&n.props.children[0]==='Piętro '&&n.props.children[1]===8);
  await floorButton.props.onClick();panel=await render();
  assert.equal(ground().focus.floor,8);
  assert.equal(posts.length,before);
  await click('Dodaj łącznik');
  assert.deepEqual(posts.at(-1).via,via);
  assert.deepEqual(ground().selected,area.cells);
  assert.deepEqual(ground().via,via);
  assert.equal(ground().connector,undefined);
  await click('Zapisz jako nowy szkic');
  assert.deepEqual(posts.at(-1).terrain,{ground:area.id,sectors:area.cells,via});
  panel.find(n=>n.props['aria-label']==='Obszar lub szkic').props.onChange({target:{value:'draft:new-id'}});panel=await render();
  assert.deepEqual(ground().via,via);
  await click('Zaproponuj pętlę');
  assert.deepEqual(posts.at(-1).via,via);
  await click('Wyczyść');
  assert.equal(button('Przelicz pętlę').props.disabled,true);
});
