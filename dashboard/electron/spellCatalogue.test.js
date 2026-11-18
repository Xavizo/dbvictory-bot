const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const ts=require('typescript');
const mod={exports:{}};
new Function('exports',ts.transpileModule(fs.readFileSync(require.resolve('../lib/spellCatalogue.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(mod.exports);
const {parseSpellCatalogue,searchSpells,findCatalogueSpell,selectCatalogueSpell,catalogueCooldown,loadSpellCatalogue,spellCategoryLabel}=mod.exports;
const row={id:85,name:'Darkness Blaster!',words:'darkness blaster',description:'',category:'Distanse',icon:'/local-spells/icons/85.png',cooldown_client_ms:1500,level:80,ki:1000,ki_percent:0,aggressive:true,unavailable:null};
test('Shinretsuha is labelled and searchable as targeted despite its Wave asset category',()=>{
  const original={...row,id:138,name:'Shinretsuha!',words:'SHINRETSUHA',category:'Wave',icon:'/local-spells/icons/11724.png',cooldown_client_ms:1000};
  const [spell]=parseSpellCatalogue({spells:[original]});
  assert.equal(spellCategoryLabel(spell),'Celowany');
  assert.deepEqual(searchSpells([spell],'celowany'),[spell]);
  const chosen=selectCatalogueSpell({words:'other',cooldown:3,aim:'self'},spell);
  assert.equal(chosen.aim,'target');assert.equal(chosen.cooldown,1);
  assert.equal(spell.category,'Wave');assert.equal(spell.icon,original.icon);
  assert.equal(selectCatalogueSpell({...chosen,cooldown:2.5},spell).cooldown,2.5);
  assert.equal(spellCategoryLabel({...spell,words:'other wave'}),'Fala');
});
test('catalogue accepts local icons and rejects malformed or remote entries',()=>{
  assert.deepEqual(parseSpellCatalogue({spells:[row]}),[row]);
  for(const patch of [{icon:'https://example.com/image.png'},{icon:'/local-spells/icons/../85.png'},{words:'a\nb'},{cooldown_client_ms:-1},{id:1.5},{aggressive:'yes'},{ki:NaN}])assert.throws(()=>parseSpellCatalogue({spells:[{...row,...patch}]}));
  for(const input of [null,{}, {spells:[row,row]}, {spells:'bad'}])assert.throws(()=>parseSpellCatalogue(input));
});
test('search matches name, words, category and ID; saved words match case-insensitively',()=>{
  const rows=[row,{...row,id:197,name:'Fly Up!',words:'fly up',category:'Support',icon:null}];
  assert.deepEqual(searchSpells(rows,' DARKNESS   distanse '),[row]);
  assert.deepEqual(searchSpells(rows,'85'),[row]);
  assert.deepEqual(searchSpells(rows,'nothing'),[]);
  assert.equal(searchSpells(rows,'').length,2);
  assert.equal(findCatalogueSpell(rows,' Darkness Blaster '),row);
});
test('choosing another spell sets catalogue seconds and preserves all combat conditions',()=>{
  const before={words:'fly up',trigger:'visible',cooldown:4.2,ki:25000,ki_unit:'value',ki_op:'>',hp:40,hp_unit:'percent',hp_op:'<=',range:4,range_op:'<=',count:3,count_op:'=',aim:'self'};
  const after=selectCatalogueSpell(before,row);
  assert.deepEqual(after,{...before,words:'darkness blaster',cooldown:1.5,aim:'target'});
  assert.equal(selectCatalogueSpell(before,{...row,category:'Area'}).aim,'self');
  assert.equal(before.words,'fly up');assert.equal(before.cooldown,4.2);
  const overridden={...after,cooldown:2.7,words:'Darkness Blaster'};
  assert.equal(selectCatalogueSpell(overridden,row),overridden);
  assert.equal(JSON.parse(JSON.stringify(overridden)).cooldown,2.7);
  assert.equal(catalogueCooldown({...row,cooldown_client_ms:100}),.2);
  assert.equal(catalogueCooldown({...row,cooldown_client_ms:500}),.5);
  assert.throws(()=>selectCatalogueSpell(before,{...row,unavailable:'Requires a parameter'}));
});
test('catalogue fetch is local, shared and retries only after a failed load',async()=>{
  const original=global.fetch;let calls=0;
  global.fetch=async url=>{assert.equal(url,'/local-spells/catalog.json');calls++;return calls===1?{ok:false}:{ok:true,json:async()=>({spells:[row]})};};
  try {
    await assert.rejects(loadSpellCatalogue());
    const [a,b]=await Promise.all([loadSpellCatalogue(),loadSpellCatalogue()]);
    assert.equal(calls,2);assert.equal(a,b);assert.deepEqual(a,[row]);
    await loadSpellCatalogue();assert.equal(calls,2);
  } finally {global.fetch=original;}
});
