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
  const area = {id:'manual:abc',name:'Brolly',visits:2,xp:900,cells:[[24,32,7],[32,32,7],[40,32,7]],drafts:[],
    eligible:true,sightings:[],needs_recheck:true};
  const points = [[25,33,7],[26,33,7],[25,33,7]];
  const auth = {authenticatedFetch: async (url, init) => {
    const body = init.body ? JSON.parse(init.body) : undefined;
    if (body) posts.push(body);
    let result;
    if (url.includes('/discoveries?')) result = {scope:'test',discoveries:[area],revision:'abc'};
    else if (url.endsWith('/grounds')) result = {scope:'test',sector_size:8,areas:[],drafts:[]};
    else if (url.endsWith('/route')) result = {points,path:points,links:[],entrance:points[0],total:2,covered:1,missing:[[24,32,6]],excluded:body.excluded??[],transitions:0,sightings:0};
    else if (url.endsWith('/drafts')) result = {saved_id:'new-id'};
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
  async function render() {
    let root;
    for (let i=0;i<5;i++) {
      cursor = 0;
      root = exports.default({initialFloor:7,onOpen:()=>{},onClose:()=>{}});
      while (effects.length) effects.shift()();
      await new Promise(done => setImmediate(done));
    }
    return nodes(root);
  }
  let panel = await render();
  assert.ok(panel.some(n => n.props.children === 'Możesz przeliczyć pętlę z zapisanej wizyty.'));
  assert.deepEqual(posts, []);
  const button = label => panel.find(n => n.type === 'button' && n.props.children === label);
  await button('Przelicz pętlę').props.onClick();
  panel = await render();
  assert.deepEqual(posts, [{scope:'test',ground:area.id}]);
  assert.ok(panel.some(n=>n.props.children==='Pętla gotowa na części expowiska · 1/2 sektorów'));
  assert.deepEqual(panel.find(n=>n.props.ground)?.props.ground.focus,{floor:7,cells:area.cells});
  await button('Zapisz jako nowy szkic').props.onClick();
  panel = await render();
  assert.deepEqual(posts[1].points, points);
  assert.equal(posts[1].id, undefined);
  assert.ok(button('Otwórz zapisany szkic'));
  const excludeButton=()=>panel.find(n=>n.type==='button'&&n.props.children?.[0]==='Pomiń sektory');
  excludeButton().props.onClick(); panel=await render();
  panel.find(n=>n.props.ground).props.ground.selectSectors([46,35,7],[32,32,7]);panel=await render();
  assert.equal(button('Otwórz zapisany szkic').props.disabled,true);
  assert.deepEqual(panel.find(n=>n.props.ground).props.ground.excluded,area.cells.slice(1));
  button('Cofnij wybór').props.onClick();panel=await render();
  assert.deepEqual(panel.find(n=>n.props.ground).props.ground.excluded,[]);
  panel.find(n=>n.props.ground).props.ground.selectSectors([32,32,7],[46,35,7]);panel=await render();
  await button('Przelicz pętlę').props.onClick();panel=await render();
  assert.deepEqual(posts.at(-1),{scope:'test',ground:area.id,excluded:area.cells.slice(1),loop_start:points[0]});
  assert.equal(button('Zapisz jako nowy szkic').props.disabled,false);
});
