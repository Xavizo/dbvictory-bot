// Main-process polling remains active in the tray. No game input or UI focus.
function visiblePlayers(data, now) {
  const map=data?.passive_map, pos=data?.player?.position;
  if (!data?.connected || !map || map.status!=='ready' || map.stale || !Array.isArray(pos) ||
      !Array.isArray(map.center) || !map.center.every((v,i)=>v===pos[i]) ||
      !Number.isFinite(map.captured_at) || now/1000-map.captured_at<0 || now/1000-map.captured_at>=2) return [];
  // alert_players contains typed Player objects, excluding LocalPlayer/NPCs/monsters.
  return [...new Set((map.cells??[]).filter(c=>c.z===pos[2]).flatMap(c=>c.alert_players??[])
    .map(p=>p.id).filter(id=>Number.isSafeInteger(id)&&id>=0x10000000&&id<0x40000000))];
}

function createSoundConsumer(playSound, now=Date.now) {
  let session=null,cursor=0,lastPlayer=-Infinity,lastMessage=-Infinity,lastTest=-Infinity,queue=[];
  let alarmId=null, lastAlarm=-Infinity;
  const allowed=(state,kind)=>{const cfg=state.config?.[kind];return cfg?.enabled===true&&cfg.sound===true&&
    (cfg.scope==='always'||(cfg.scope==='cavebot'&&state.cavebot_active===true));};
  return {
    consume(state, players=[]) {
      if(!state||typeof state.session!=='string'||!Number.isSafeInteger(state.latest_id)||!Array.isArray(state.events))return;
      if(state.session!==session){session=state.session;alarmId=null;cursor=state.latest_id;queue=[];lastPlayer=-Infinity;}
      if(state.paused){
        cursor=state.latest_id;queue=[];lastPlayer=-Infinity;
        return;
      }
      const silencedThrough=state.silenced_through??0;
      const kiAlarm=state.ki_alarm_id>silencedThrough&&allowed(state,'ki_loss')?state.ki_alarm_id:null;
      const activeAlarm=state.vitals_alarm_id??kiAlarm;
      if(activeAlarm!==alarmId){alarmId=activeAlarm;lastAlarm=-Infinity;}
      if(alarmId!==null && now()-lastAlarm>=2000 && playSound('vitals')!==false){
        lastAlarm=now();
        return;
      }
      cursor=Math.max(cursor,silencedThrough);
      if(state.latest_id>cursor){
        for(const e of state.events){
          const age=now()/1000-e.at;
          if(Number.isSafeInteger(e.id)&&e.id>cursor&&e.id<=state.latest_id&&['private','default'].includes(e.kind)&&
              allowed(state,e.kind)&&age>=0&&age<10)queue.push({id:e.id,kind:e.kind,expires:now()+30000});
        }
        cursor=Math.max(cursor,state.latest_id);queue=queue.slice(-100);
      }
      queue=queue.filter(e=>e.id>silencedThrough&&e.expires>=now()&&allowed(state,e.kind));
      if(queue.length&&now()-lastMessage>=700){if(playSound('message')!==false){queue.shift();lastMessage=now();}return;}
      if(!players.length||!allowed(state,'player')){lastPlayer=-Infinity;return;}
      if(now()-lastPlayer>=1500&&now()-lastMessage>=700&&playSound('player')!==false)lastPlayer=now();
    },
    testSound(){if(now()-lastTest<1000)return false;lastTest=now();playSound('player');return true;},
  };
}

function startAlertSounds({token,fetchImpl,beep,onHuntReady,onMute,setTimer=setTimeout,clearTimer=clearTimeout,now=Date.now}) {
  const consumer=createSoundConsumer(beep,now);let stopped=false,timer,request;
  let notifiedRecording=null, automationPaused=false;
  // Older engines have independent alert preferences but no session-wide mute.
  let panelPaused=true, alertSession=null, silencedThrough=0, lastId=0, resumedAt=null;
  const muted=()=>panelPaused || automationPaused;
  function panelPause(value) {
    const wasMuted=muted();
    if (value !== undefined) {
      if (typeof value !== 'boolean') throw new TypeError('Invalid alert pause');
      if (value!==panelPaused) {
        panelPaused=value;
        silencedThrough=lastId;
        resumedAt=value?null:now()/1000;
      }
    }
    if(!wasMuted && muted())onMute?.();
    return {paused:muted(), session:alertSession, through:silencedThrough};
  }
  async function poll(){
    if(stopped||!token)return;
    try{
      request=new AbortController();const timeout=setTimer(()=>request?.abort(),2000);
      try{
        const response=await fetchImpl('http://127.0.0.1:8089/api/state',{
          headers:{Authorization:`Bearer ${token}`,Origin:'http://127.0.0.1:4747'},cache:'no-store',redirect:'error',signal:request.signal});
        if(response.ok){
          const data=await response.json();
          if(stopped)return;
          const wasMuted=muted();
          automationPaused=data.automation_paused===true || data.alerts?.automation_paused===true;
          if(data.alerts){
            let alerts=data.alerts;
            if(alertSession!==alerts.session){
              if(alertSession!==null)panelPaused=true;
              alertSession=alerts.session;silencedThrough=0;resumedAt=null;
            }
            if(typeof alerts.paused!=='boolean'){
              lastId=alerts.latest_id;
              if(panelPaused)silencedThrough=lastId;
              else if(resumedAt!==null){
                for(const e of alerts.events??[]){
                  if(e.at<=resumedAt)silencedThrough=Math.max(silencedThrough,e.id);
                }
              }
              alerts={...alerts,paused:panelPaused,silenced_through:silencedThrough};
            } else {
              panelPaused=alerts.paused;
            }
            if(automationPaused)alerts={...alerts,paused:true};
            consumer.consume(alerts,alerts.paused?[]:visiblePlayers(data,now()));
          }
          if(!wasMuted && muted())onMute?.();
          const recording=data.cavebot?.recording;
          if(!recording?.active || recording.mode!=='adaptive')notifiedRecording=null;
          else if(!stopped && recording.name && recording.hunt_progress?.stage===4 && notifiedRecording!==recording.name){
            notifiedRecording=recording.name;
            if(data.alerts && !muted())onHuntReady?.();
          }
        }
      }finally{clearTimer(timeout);request=null;}
    }catch{/* No chat or credentials in logs. No repetition without a fresh response. */}
    if(!stopped){timer=setTimer(poll,750);timer?.unref?.();}
  }
  void poll();
  return {panelPause,testSound:()=>!muted()&&consumer.testSound(),stop(){stopped=true;request?.abort();clearTimer(timer);}};
}
module.exports={createSoundConsumer,startAlertSounds,visiblePlayers};
