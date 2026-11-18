const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const code=ts.transpileModule(fs.readFileSync(require.resolve('../app/SessionStats.tsx'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
const exported={};new Function('exports','require',code)(exported,require);
const base={status:'active',elapsed_seconds:3661,start_level:389,level:391,levels_gained:2,xp_gained:60000,xp_per_hour:3600000,window_seconds:60,window_target_seconds:900,sample_age_seconds:0};
const render=stats=>renderToStaticMarkup(React.createElement(exported.default,{stats}));
const modelCode = ts.transpileModule(fs.readFileSync(require.resolve('../lib/panelSession.ts'),'utf8'),
  {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const model = {};
new Function('exports',modelCode)(model);
const sample = (experience,level=390,session=2,stats_age=0) =>
  ({connected:true, commands:{session}, player:{experience,level,stats_age}});

test('older API: discard initial unknown XP and calculate a rolling net rate', () => {
  const panel = new model.PanelSession();
  assert.equal(panel.observe(sample(0),0).session_statistics.xp_gained,null);
  assert.equal(panel.observe(sample(200000),1).session_statistics.xp_per_hour,null);
  let stats;
  for (let t=2;t<=1000;t++) stats = panel.observe(sample(250000+t*10,391),t).session_statistics;
  assert.equal(stats.elapsed_seconds,1000);
  assert.equal(stats.levels_gained,1);
  assert.equal(stats.window_seconds,900);
  assert.equal(stats.xp_per_hour,36000);
});

test('network gaps, logout, relogin and backend collection have separate boundaries', () => {
  const panel = new model.PanelSession();
  panel.observe(sample(900),0);
  assert.equal(panel.observe(sample(800,389),1).session_statistics.xp_per_hour,-360000);
  assert.equal(panel.observe(sample(800,389,2,8),9).session_statistics.status,'stale');
  const resumed = panel.observe(sample(950),12).session_statistics;
  assert.equal(resumed.xp_per_hour,null);
  assert.equal(resumed.xp_gained,50);
  assert.equal(panel.observe({connected:false},20).session_statistics.elapsed_seconds,12);
  assert.equal(panel.observe(sample(500,10,3),25).session_statistics.levels_gained,0);
  const authoritative = {...sample(500),session_statistics:base};
  assert.equal(panel.observe(authoritative,26),authoritative);
});

test('session strip identifies a short rate sample and the level gain',()=>{
 const html=render(base);
 assert.match(html,/01:01:01/);assert.match(html,/>\+2</);assert.match(html,/Próbka 1:00 \/ 15:00/);
 assert.match(html,/389 → obecny 391/);assert.match(html,/XP\/h/);
 assert.doesNotMatch(render({...base,window_seconds:900}),/Próbka/);
});
test('missing and stale XP are not shown as a measured zero rate',()=>{
 assert.equal(render(undefined),'');
 const html=render({...base,status:'stale',xp_per_hour:null});
 assert.match(html,/Brak świeżego odczytu/);assert.match(html,/Czekam na świeżą próbkę XP/);
 assert.doesNotMatch(html,/3,6/);
});
