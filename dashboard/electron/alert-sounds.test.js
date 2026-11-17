const test=require('node:test'),assert=require('node:assert/strict');
const {createSoundConsumer,startAlertSounds,visiblePlayers}=require('./alert-sounds');
const {createNotificationAudio}=require('./notification-audio');
const config={private:{enabled:true,sound:true,scope:'always'},default:{enabled:true,sound:true,scope:'always'},player:{enabled:true,sound:true,scope:'cavebot'}};
const snapshot=(events=[],extra={})=>({session:'one',latest_id:events.at(-1)?.id??0,events,config,cavebot_active:true,...extra});
const event=(id,extra={})=>({id,kind:'private',at:100,sound:true,...extra});

test('player repeats while visible, stops on departure, and ignores history acknowledgement',()=>{
 let now=100000,sounds=[];const c=createSoundConsumer(k=>sounds.push(k),()=>now);
 c.consume(snapshot(),[12]);assert.deepEqual(sounds,['player']);
 now+=4000;c.consume(snapshot(),[12]);assert.equal(sounds.length,1);
 now+=1000;c.consume(snapshot([],{unread:0}),[12]);assert.equal(sounds.length,2);
 now+=5000;c.consume(snapshot(),[]);assert.equal(sounds.length,2);
 c.consume(snapshot(),[13]);assert.equal(sounds.length,3);
 now+=5000;c.consume(snapshot([],{cavebot_active:false}),[13]);assert.equal(sounds.length,3);
 c.consume(snapshot([],{config:{...config,player:{...config.player,sound:false}}}),[13]);assert.equal(sounds.length,3);
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
  assert.equal(p.panelPause(true).paused,true);
  state=snapshot([event(1)]);await tick();
  assert.equal(p.panelPause().through,1);
  assert.equal(p.testSound(),false);
  p.panelPause(false);await tick();
  state=snapshot([event(1),event(2,{at:t/1000})]);await tick();
  assert.deepEqual(heard,['message']);
  assert.throws(()=>p.panelPause('on'),TypeError);
  p.stop();
});
test('fixed authenticated state polling cancels on quit without changing actions',async()=>{
 const calls=[],timers=[];
 const p=startAlertSounds({token:'synthetic',beep:()=>{},fetchImpl:async(url,opts)=>{calls.push({url,opts});return {ok:true,json:async()=>({alerts:snapshot()})};},setTimer:(fn,ms)=>{const t={fn,ms};timers.push(t);return t;},clearTimer:()=>{}});
 await new Promise(r=>setImmediate(r));assert.equal(calls.length,1);
 assert.equal(calls[0].url,'http://127.0.0.1:8089/api/state');assert.equal(calls[0].opts.headers.Authorization,'Bearer synthetic');assert.equal(calls[0].opts.redirect,'error');
 p.stop();await timers.find(t=>t.ms===750).fn();assert.equal(calls.length,1);
});
test('audio uses only fixed local WAVs, hidden process, escaped path, and no overlapping children',()=>{
 const calls=[];let done,killed=0;
 const audio=createNotificationAudio({fallback:()=>{},platform:'win32',directory:"C:\\test's app",systemRoot:'C:\\Windows',run:(exe,args,opts,cb)=>{calls.push({exe,args,opts});done=cb;return {kill(){killed++;}};}});
 assert.equal(audio.play('message'),true);assert.equal(audio.play('player'),false);
 assert.equal(calls[0].opts.windowsHide,true);assert.ok(calls[0].args.includes('Hidden'));
 const script=Buffer.from(calls[0].args.at(-1),'base64').toString('utf16le');assert.match(script,/test''s app/);assert.match(script,/message\.wav/);
 done();audio.play('untrusted command');assert.match(Buffer.from(calls[1].args.at(-1),'base64').toString('utf16le'),/player\.wav/);
 audio.stop();assert.equal(killed,1);assert.equal(audio.play('message'),false);
});
