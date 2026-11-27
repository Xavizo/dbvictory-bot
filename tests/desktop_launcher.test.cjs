"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs/promises");
const test = require("node:test");
const vm = require("node:vm");
// The launcher page's rendering. Its logic is tested in tests/test_desktop_app.py.

async function launcherRenderer() {
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
  }};
  const env = vm.createContext({document,window:{desktop:{onState(){},state:()=>({then(){}})}}});
  for(const file of ["effects.js","render.js"])
    vm.runInContext(await fs.readFile(path.join(desktop,file),"utf8"),env);
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

