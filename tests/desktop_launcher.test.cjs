"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs/promises");
const test = require("node:test");
const vm = require("node:vm");
// The launcher page's rendering. Its logic is tested in tests/test_desktop_app.py.

async function launcherRenderer(language="pl") {
  const desktop = path.resolve(__dirname,"../launcher/desktop");
  class Element {
    children = [];
    style = {setProperty(){}};
    classList = {toggle(){}};
    append(...items){this.children.push(...items);}
    replaceChildren(){this.children=[];}
    get childElementCount(){return this.children.length;}
    setAttribute(){}
    addEventListener(){}
  }
  const nodes = new Map();
  const document = {createElement:()=>new Element(),createTextNode:text=>({textContent:text}),querySelector(selector){
    if(!nodes.has(selector))nodes.set(selector,new Element());
    return nodes.get(selector);
  },querySelectorAll:()=>[],documentElement:{}};
  const pending={then(){}};
  const env = vm.createContext({document,window:{desktop:{onState(){},state:()=>pending,texts:()=>pending}}});
  for(const file of ["effects.js","render.js"])
    vm.runInContext(await fs.readFile(path.join(desktop,file),"utf8"),env);
  env.useTexts({language,texts:JSON.parse(await fs.readFile(path.join(desktop,"i18n",language+".json"),"utf8"))});
  return {env,nodes,desktop};
}

test("the connection button relies on the user and is always available when prepared", async () => {
  const {env,nodes}=await launcherRenderer();
  const state={phase:"prepared",folder:"",text:"",changes:[]};
  for (const readOnly of [false,true,false]) {
    env.update({...state,readOnly});
    const button=nodes.get("#primary");
    assert.equal(button.textContent,"Połącz z grą");
    assert.equal(button.hidden,false);
    assert.equal(button.disabled,readOnly);
  }
});

test("history preview shows seven titles across releases and keeps all details", async () => {
  const {env,nodes,desktop}=await launcherRenderer();
  const releases = JSON.parse(await fs.readFile(path.join(desktop,"release-notes.json"),"utf8"));
  env.drawHistory(releases);
  const list=nodes.get("#changes");
  assert.deepEqual(list.children.map(li=>li.children[1].textContent),
    releases.flatMap(r=>r.changes).slice(0,7).map(c=>c.title));
  assert.deepEqual(list.children.map(li=>li.children[0].childElementCount),[1,2,3,4,5,6,7]);
  assert.equal(nodes.get("#releases").childElementCount,releases.length);
  env.drawHistory([]);
  assert.equal(list.childElementCount,0);
});

test("the renderer offers install and postpone for a GitHub release and a folder picker without a client", async () => {
  const {env,nodes}=await launcherRenderer();
  env.update({phase:"available",folder:"",text:"Dostępne wydanie",changes:[],canInstall:true,canDefer:true});
  assert.equal(nodes.get("#primary").textContent,"Zainstaluj aktualizację");
  assert.equal(nodes.get("#secondary").textContent,"Odłóż wydanie");
  env.update({phase:"ready",folder:"",text:"",changes:[],installed:false});
  assert.equal(nodes.get("#primary").textContent,"Wybierz folder klienta");
  env.update({phase:"clientError",folder:"C:\\Gra",text:"",changes:[],installed:true,clientOutdated:true});
  assert.equal(nodes.get("#primary").textContent,"Start");
  assert.equal(nodes.get("#secondary").textContent,"Sprawdź aktualizacje");
});


test("an update shows its stage, size and estimate on a filled bar", async () => {
  const {env,nodes}=await launcherRenderer();
  const progress={stage:"extract",step:2,steps:3,percent:40,label:"Rozpakowywanie: 40 z 100 plików"};
  env.update({phase:"applying",folder:"",text:"Aktualizacja do v2.4.0",changes:[],progress});
  assert.equal(nodes.get(".fill").style.width,"40%");
  const steps=nodes.get("#steps").children;
  assert.deepEqual(steps.map(li=>li.className),["done","active update-label",undefined]);
  assert.equal(steps[1].children[1].textContent,"Rozpakowywanie: 40 z 100 plików");
  assert.equal(steps[1].children[2].textContent,"40%");
  env.update({phase:"applying",folder:"",text:"Przygotowywanie…",changes:[],progress:null});
  assert.equal(nodes.get(".fill").style.width,"");
});

test("the settings chip names every connected AI and hides when none is", async () => {
  const {env,nodes}=await launcherRenderer();
  const target=(label,connected)=>({label,connected,available:true});
  env.drawAi({status:{codex:target("Codex",true),"claude-desktop":target("Claude Desktop",false),"claude-code":target("Claude Code",true)}});
  assert.equal(nodes.get("#aiChip").hidden,false);
  assert.equal(nodes.get("#aiSummary").textContent,"Zintegrowane AI: Codex, Claude Code");
  assert.equal(nodes.get("#aiTargets").childElementCount,3);
  env.drawAi({status:{codex:target("Codex",false)}});
  assert.equal(nodes.get("#aiChip").hidden,true);
});

test("the test-build switch shows only for release installs and reflects the channel", async () => {
  const {env,nodes}=await launcherRenderer();
  const state={phase:"ready",folder:"",text:"",changes:[],installed:true};
  env.update({...state,channel:null});
  assert.equal(nodes.get("#channelRow").hidden,true);
  env.update({...state,channel:"test"});
  assert.equal(nodes.get("#channelRow").hidden,false);
  assert.equal(nodes.get("#testChannel").checked,true);
  env.update({...state,channel:"stable",readOnly:true});
  assert.equal(nodes.get("#testChannel").checked,false);
  assert.equal(nodes.get("#testChannel").disabled,true);
});

test("the renderer speaks English from the same state once the player switches", async () => {
  const {env,nodes}=await launcherRenderer("en");
  env.update({phase:"prepared",folder:"",text:"",changes:[]});
  assert.equal(nodes.get("#primary").textContent,"Connect to the game");
  assert.equal(nodes.get("#heading").textContent,"Character selection");
  env.update({phase:"ready",folder:"D:\\Gry",text:"",changes:[],installed:true,clientOpen:false});
  assert.equal(nodes.get("#badge").textContent,"Installed");
});

test("both launcher languages name the same texts", async () => {
  const dir=path.resolve(__dirname,"../launcher/desktop/i18n");
  const pl=JSON.parse(await fs.readFile(path.join(dir,"pl.json"),"utf8")), en=JSON.parse(await fs.readFile(path.join(dir,"en.json"),"utf8"));
  assert.deepEqual(Object.keys(en).sort(),Object.keys(pl).sort());
  for (const key of Object.keys(pl)) assert.deepEqual((en[key].match(/\{\w+\}/g)||[]).sort(),(pl[key].match(/\{\w+\}/g)||[]).sort(),key);
});
