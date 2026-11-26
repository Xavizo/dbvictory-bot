const test=require('node:test'),assert=require('node:assert/strict');
const {createSoundConsumer,startAlertSounds,visiblePlayers}=require('./alert-sounds');
const {createNotificationAudio}=require('./notification-audio');
const config={private:{enabled:true,sound:true,scope:'always'},default:{enabled:true,sound:true,scope:'always'},player:{enabled:true,sound:true,scope:'cavebot'}};
const snapshot=(events=[],extra={})=>({session:'one',latest_id:events.at(-1)?.id??0,events,config,cavebot_active:true,...extra});
const event=(id,extra={})=>({id,kind:'private',at:100,sound:true,...extra});

test('player repeats while visible, stops on departure, and ignores history acknowledgement',()=>{
 let now=100000,sounds=[];const c=createSoundConsumer(k=>sounds.push(k),()=>now);
 c.consume(snapshot(),[12]);assert.deepEqual(sounds,['player']);
 now+=1499;c.consume(snapshot(),[12]);assert.equal(sounds.length,1);
 now+=1;c.consume(snapshot([],{unread:0}),[12]);assert.equal(sounds.length,2);
 now+=1500;c.consume(snapshot(),[12]);assert.equal(sounds.length,3);
 now+=1500;c.consume(snapshot(),[]);assert.equal(sounds.length,3);
 c.consume(snapshot(),[13]);assert.equal(sounds.length,4);
 now+=1500;c.consume(snapshot([],{cavebot_active:false}),[13]);assert.equal(sounds.length,4);
 c.consume(snapshot([],{config:{...config,player:{...config.player,sound:false}}}),[13]);assert.equal(sounds.length,4);
});
test('each new chat message has one distinct sound, including backend-coalesced messages',()=>{
 let now=100000,sounds=[];const c=createSoundConsumer(k=>sounds.push(k),()=>now);
 c.consume(snapshot([event(1)]));assert.equal(sounds.length,0);
 const s=snapshot([event(1),event(2),event(3,{sound:false,kind:'default'})]);
 c.consume(s);now+=750;c.consume(s);now+=5000;c.consume(s);
 assert.deepEqual(sounds,['message','message']);
 c.consume(snapshot([event(1)],{session:'new'}));assert.equal(sounds.length,2);
});
test('busy audio retries messages, current preferences cancel queued messages, stale chat is skipped',()=>{
 let now=100000,busy=true,sounds=0;const c=createSoundConsumer(()=>{if(busy)return false;sounds++;return true;},()=>now);
 c.consume(snapshot());c.consume(snapshot([event(1)]));assert.equal(sounds,0);
 busy=false;now+=750;c.consume(snapshot([event(1)]));assert.equal(sounds,1);
 c.consume(snapshot([event(2),event(3)]));
 now+=750;c.consume(snapshot([event(3)],{config:{...config,private:{...config.private,sound:false}}}));assert.equal(sounds,1);
 c.consume(snapshot([event(4,{at:60})]));assert.equal(sounds,1);
});
test('global pause silences chat and player repeats and discards queued sounds even between polls',()=>{
 for(const pollWhilePaused of [false,true]){
  let now=100000;const sounds=[],c=createSoundConsumer(k=>sounds.push(k),()=>now);
  c.consume(snapshot());
  c.consume(snapshot([event(1),event(2)]));
  assert.deepEqual(sounds,['message']);
  if(pollWhilePaused){
   now+=5000;c.consume(snapshot([event(1),event(2)],{paused:true,silenced_through:2}),[12]);
   assert.deepEqual(sounds,['message']);
  }
  now+=750;c.consume(snapshot([event(1),event(2)],{paused:false,silenced_through:2}));
  assert.deepEqual(sounds,['message']);
  c.consume(snapshot([event(3,{at:now/1000})],{paused:false,silenced_through:2}));
  now+=5000;c.consume(snapshot([event(3)],{paused:false,silenced_through:2}),[12]);
  assert.deepEqual(sounds,['message','message','player']);
 }
});
test('only fresh aligned map with typed players permits repeat sound',()=>{
 const data={connected:true,player:{position:[10,10,7]},passive_map:{status:'ready',stale:false,captured_at:100,center:[10,10,7],cells:[{z:7,alert_players:[{id:0x10000002}]},{z:6,alert_players:[{id:0x10000003}]}]}};
 assert.deepEqual(visiblePlayers(data,100000),[0x10000002]);
 for(const map of [{...data.passive_map,stale:true},{...data.passive_map,captured_at:90},{...data.passive_map,center:[11,10,7]}])assert.deepEqual(visiblePlayers({...data,passive_map:map},100000),[]);
 assert.deepEqual(visiblePlayers({...data,connected:false},100000),[]);
});

test('panel mute on an older engine silences the tray and resumes on new chat',async()=>{
  let t=100000,state=snapshot();
  const timers=[],heard=[];
  const p=startAlertSounds({token:'test',now:()=>t,beep:k=>heard.push(k),
    fetchImpl:async()=>({ok:true,json:async()=>({alerts:state})}),
    setTimer:(fn,ms)=>{timers.push({fn,ms});return 0;},clearTimer:()=>{}});
  const tick=async()=>{
    t+=750;
    await timers.splice(timers.findIndex(a=>a.ms===750),1)[0].fn();
  };
  await new Promise(r=>setImmediate(r));
  assert.equal(p.panelPause().paused,true);
  state=snapshot([event(1)]);await tick();
  assert.equal(p.panelPause().through,1);
  assert.equal(p.testSound(),false);
  p.panelPause(false);await tick();
  state=snapshot([event(1),event(2,{at:t/1000})]);await tick();
  assert.deepEqual(heard,['message']);
  state=snapshot([],{session:'two'});await tick();
  assert.equal(p.panelPause().paused,true);
  assert.throws(()=>p.panelPause('on'),TypeError);
  p.stop();
});
test('a restarted panel uses the engine pause and suppresses sound, flashes and backlog until resumed',async()=>{
  let t=100000, state=snapshot([event(1)],{paused:true,silenced_through:1}),poll;
  const heard=[];
  const data={connected:true,player:{position:[1,1,7]},passive_map:{status:'ready',center:[1,1,7],
    cells:[{z:7,alert_players:[{id:0x10000020}]}]}};
  const panel=startAlertSounds({token:'test',now:()=>t,beep:k=>heard.push(k),
    fetchImpl:async()=>({ok:true,json:async()=>({...data,alerts:state,passive_map:{...data.passive_map,captured_at:t/1000}})}),
    setTimer:(fn,ms)=>{if(ms===750)poll=fn;return 0;},clearTimer:()=>{}});
  assert.equal(panel.testSound(),false);
  await new Promise(resolve=>setImmediate(resolve));
  t+=1500;await poll();
  assert.deepEqual(heard,[]);
  state={...state,paused:false};await poll();
  assert.deepEqual(heard,['player']);
  t+=1500;state=snapshot([event(2,{at:t/1000})],{paused:false,silenced_through:1});await poll();
  assert.deepEqual(heard,['player','message']);
  state=snapshot([],{session:'new',paused:true});t+=1500;await poll();
  assert.deepEqual(heard,['player','message']);assert.equal(panel.testSound(),false);
  panel.stop();
});
test('fixed authenticated state polling cancels on quit without changing actions',async()=>{
 const calls=[],timers=[];
 const p=startAlertSounds({token:'synthetic',beep:()=>{},fetchImpl:async(url,opts)=>{calls.push({url,opts});return {ok:true,json:async()=>({alerts:snapshot()})};},setTimer:(fn,ms)=>{const t={fn,ms};timers.push(t);return t;},clearTimer:()=>{}});
 await new Promise(r=>setImmediate(r));assert.equal(calls.length,1);
 assert.equal(calls[0].url,'http://127.0.0.1:8089/api/state');assert.equal(calls[0].opts.headers.Authorization,'Bearer synthetic');assert.equal(calls[0].opts.redirect,'error');
 p.stop();await timers.find(t=>t.ms===750).fn();assert.equal(calls.length,1);
});
test('adaptive hunt readiness notifies once per recording and respects mute',async()=>{
  let rec={active:true,mode:'adaptive',name:'first',hunt_progress:{stage:3}},paused=false,count=0,poll;
  const sounds=startAlertSounds({token:'test',beep:()=>{},onHuntReady:()=>{count++;},
    fetchImpl:async()=>({ok:true,json:async()=>({alerts:snapshot([],{paused}),cavebot:{recording:rec}})}),
    setTimer:(fn,ms)=>{if(ms===750)poll=fn;return 0;},clearTimer:()=>{}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(count,0);
  rec.hunt_progress.stage=4;await poll();await poll();assert.equal(count,1);
  rec.hunt_progress.stage=3;await poll();rec.hunt_progress.stage=4;await poll();assert.equal(count,1);
  rec.active=false;await poll();rec.active=true;rec.name='second';paused=true;await poll();
  paused=false;await poll();assert.equal(count,1);
  rec.name='third';await poll();assert.equal(count,2);
  sounds.stop();
});
test('both notification kinds use the same hidden Windows helper; busy or stopped signals stay silent',()=>{
 const calls=[];let done,killed=0;
 const directory="C:\\test's app";
 const audio=createNotificationAudio({fallback:()=>{},platform:'win32',directory,run:(exe,args,opts,cb)=>{calls.push({exe,args,opts});done=cb;return {kill(){killed++;}};}});
 assert.equal(audio.play('message'),true);assert.equal(audio.play('player'),false);
 assert.equal(calls[0].opts.windowsHide,true);
 assert.deepEqual(calls[0].args,['-I','-B',require('node:path').join(directory,'windows_alert.py'),'message']);
 assert.match(calls[0].exe,/pythonw\.exe$/);
 done();audio.play('untrusted command');assert.equal(calls[1].args.at(-1),'player');
 audio.stop();assert.equal(killed,1);assert.equal(audio.play('message'),false);
});

test('an unread preview stays dismissible even after four seconds; a read alert has no preview',()=>{
 const fs=require('node:fs'),ts=require('typescript'),React=require('react');
 const {renderToStaticMarkup}=require('react-dom/server');
 const source=fs.readFileSync(require.resolve('../app/AlertBell.tsx'),'utf8');
 const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,
   target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const mod={exports:{}};
 new Function('exports','require',js)(mod.exports,name=>name==='@/lib/api'?{}:require(name));
 const render=(age,unread=1,paused=false)=>renderToStaticMarkup(React.createElement(mod.exports.default,{
   connected:true,onRefresh(){},alerts:snapshot([event(1,{at:Date.now()/1000-age,sender:'Gracz',text:'Test'})],{unread,paused})}));
 assert.doesNotMatch(render(0,1,true),/class="alert-peek"|class="alert-count"/);
 assert.match(render(0,1,true),/aria-label="Wznów alerty"/);
 assert.match(render(0),/class="alert-peek"/);
 const after=render(5);
 assert.match(after,/class="alert-peek"/);
 assert.match(after,/aria-label="Zamknij podgląd alertu"/);
 assert.doesNotMatch(render(5,0),/class="alert-peek"/);
 assert.match(after,/class="alert-count">1</);
});

test('vitals loss sounds on first poll, repeats while active and stops on recovery or mute',()=>{
  let time=100000;
  const sounds=[], consumer=createSoundConsumer(kind=>sounds.push(kind),()=>time);
  const alarm=snapshot([event(4,{kind:'vitals'})],{vitals_alarm_id:4});
  consumer.consume(alarm);
  time+=1500;consumer.consume(alarm);
  time+=500;consumer.consume(alarm);
  time+=3000;consumer.consume({...alarm,paused:true});
  consumer.consume({...alarm,vitals_alarm_id:null});
  assert.deepEqual(sounds,['vitals','vitals']);
  consumer.consume({...alarm,vitals_alarm_id:6});
  assert.deepEqual(sounds,['vitals','vitals','vitals']);
});

test('Ki alarm repeats while active, respecting mute, scope and the expiry from the engine',()=>{
  let time=100000;
  const sounds=[], c=createSoundConsumer(k=>sounds.push(k),()=>time);
  const row={enabled:true,sound:true,scope:'always'};
  const state=snapshot([event(1,{kind:'ki_loss'})],{ki_alarm_id:1,config:{...config,ki_loss:row}});
  c.consume(state);
  for(let i=1;i<=5;i++){
    time+=2000;
    c.consume(i===5?{...state,ki_alarm_id:null}:state);
  }
  assert.deepEqual(sounds,Array(5).fill('vitals'));
  c.consume({...state,paused:true});
  c.consume({...state,silenced_through:1});
  c.consume({...state,config:{...config,ki_loss:{...row,sound:false}}});
  c.consume({...state,cavebot_active:false,config:{...config,ki_loss:{...row,scope:'cavebot'}}});
  assert.equal(sounds.length,5);
  c.consume({...state,ki_alarm_id:2,latest_id:2});
  assert.equal(sounds.length,6);
});
