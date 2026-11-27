"use strict";
const headings = {
  checking:"Sprawdzanie aktualizacji", applying:"Aktualizacja", ready:"Klient gry", busy:"Sesja jest zajęta",
  available:"Dostępna aktualizacja", recovery:"Aktualizacja do dokończenia", problem:"Launcher wymaga uwagi",
  preparing:"Klient gry", detecting:"Uruchamianie", clientError:"Uruchamianie", starting:"Uruchamianie",
  panel:"Uruchamianie", waiting:"Oczekiwanie", closing:"Zamykanie sesji", closed:"Sesja zakończona", running:"Panel gotowy",
  prepared:"Wybór postaci",connecting:"Przygotowanie połączenia"
};
const checkpoints = [
  {key:"client", pending:"Klient DBVictory", active:"Wykrywanie klienta DBVictory…", done:"Wykryto klienta DBVictory"},
  {key:"engine", pending:"Silnik bota", active:"Uruchamianie silnika…", done:"Silnik gotowy"},
  {key:"panel", pending:"Główny panel", active:"Otwieranie głównego panelu…", done:"Panel gotowy"}
];
const updateStages = [
  {key:"download", pending:"Pobieranie", done:"Pobrano"},
  {key:"extract", pending:"Rozpakowywanie", done:"Rozpakowano"},
  {key:"apply", pending:"Instalowanie plików", done:"Zainstalowano"}
];
let lastNotes;

function button(id,label,action,disabled=false) {
  const el=$(id);
  el.hidden=!label;
  el.textContent=label||"";
  el.disabled=disabled;
  el.onclick=action?()=>window.desktop.act(action):null;
}

function drawHistory(releases) {
  const key=JSON.stringify(releases);
  if (key===lastNotes) return;
  lastNotes=key;
  const list=$("#changes"), history=$("#releases");
  list.replaceChildren();history.replaceChildren();
  for (const item of releases.flatMap(release=>release.changes).slice(0,7)) {
    const li=document.createElement("li"), orb=document.createElement("span"), summary=document.createElement("span");
    orb.className="orb";orb.setAttribute("aria-hidden","true");
    for (const [x,y] of layouts[list.childElementCount]) {
      const star=document.createElement("i");
      star.className="star";star.style.left=x+"%";star.style.top=y+"%";orb.append(star);
    }
    summary.className="summary";summary.textContent=item.title;li.append(orb,summary);list.append(li);
  }
  for (const release of releases) {
    const section=document.createElement("section"), h=document.createElement("h3"), ul=document.createElement("ul");
    h.textContent=release.version;
    for (const item of release.changes) {
      const li=document.createElement("li"), title=document.createElement("strong"), detail=document.createElement("p");
      title.textContent=item.title;detail.textContent=item.detail;li.append(title,detail);ul.append(li);
    }
    section.append(h,ul);history.append(section);
  }
}

function drawUpdateSteps(progress) {
  const steps=$("#steps"), position=progress.step-1;
  steps.replaceChildren();
  for(const [i,item] of updateStages.entries()) {
    const li=document.createElement("li"), icon=document.createElement("i");
    icon.className="stepicon";icon.setAttribute("aria-hidden","true");
    let label=item.pending;
    if(i<position) {li.className="done";icon.textContent="✓";label=item.done;}
    else if(i===position) {li.className="active update-label";label=progress.label;}
    li.append(icon,document.createTextNode(label));
    if(i===position){const pct=document.createElement("small");pct.textContent=[progress.rate,progress.percent+"%"].filter(Boolean).join(" · ");li.append(pct);}
    steps.append(li);
  }
}

function drawSteps(s) {
  if(s.progress) return drawUpdateSteps(s.progress);
  const failed=checkpoints.findIndex(step=>step.key===s.failedStep);
  const position=failed>=0?failed:["prepared","connecting"].includes(s.phase)?1:["detecting","starting","panel","running"].indexOf(s.phase);
  const steps=$("#steps");
  steps.replaceChildren();
  if(position<0) return;
  for(const [i,item] of checkpoints.entries()) {
    const li=document.createElement("li"), icon=document.createElement("i");
    icon.className="stepicon";icon.setAttribute("aria-hidden","true");
    let label=item.pending;
    if(i<position) {li.className="done";icon.textContent="✓";label=item.done;}
    else if(i===failed) {li.className="failed";icon.textContent="×";label=s.text;}
    else if(i===position && s.phase!=="prepared") {li.className="active";label=item.key==="client"||s.phase==="connecting"?s.text:item.active;}
    li.append(icon,document.createTextNode(label));steps.append(li);
  }
}

let channelBusy=false;
function drawChannel(s) {
  const box=$("#testChannel");
  $("#channelRow").hidden=s.channel==null;
  if(!channelBusy) box.checked=s.channel==="test";
  box.disabled=channelBusy||!!s.readOnly;
}
async function changeChannel() {
  const box=$("#testChannel");
  if(channelBusy) return;
  channelBusy=true;box.disabled=true;
  $("#channelMessage").textContent="Zapisywanie…";
  try {
    await window.desktop.channel(box.checked?"test":"stable");
    $("#channelMessage").textContent=box.checked?"Wersje testowe włączone. Sprawdzanie aktualizacji…":"Wersje testowe wyłączone.";
  } catch (error) { box.checked=!box.checked; $("#channelMessage").textContent=error.message; }
  finally { channelBusy=false; window.desktop.state().then(drawChannel); }
}

function update(s) {
  const stage=s.phase, idle=stage==="ready"||stage==="clientError";
  drawChannel(s);
  $("#version").textContent=s.version||"Sprawdzanie wersji…";
  $("#heading").textContent=headings[stage]||"Launcher";
  drawHistory(s.changes);
  $("#more").hidden=!s.changes.length;
  $("#emptyNotes").hidden=!!s.changes.length;
  $("#emptyNotes").textContent=s.notesError?"Nie udało się wczytać historii zmian.":s.version?"Brak opisanych zmian dla tego wydania.":"Wczytywanie historii…";
  $("#folderRow").hidden=!idle;
  $("#folderPath").textContent=s.folder;$("#folderPath").title=s.folder;$("#folderEdit").hidden=s.readOnly;
  const badge=$("#badge");
  badge.hidden=stage!=="ready";badge.classList.toggle("absent",!s.clientOpen);
  badge.textContent=s.clientOpen?"Uruchomiony":s.installed?"Zainstalowany":"Wybierz folder";
  drawSteps(s);
  const progress=$("#progress");
  progress.hidden=!["preparing","checking","applying","detecting","starting","panel","connecting"].includes(stage);
  progress.classList.toggle("indeterminate",!s.progress&&!["starting","panel"].includes(stage));
  $(".fill").style.width=s.progress?s.progress.percent+"%":stage==="starting"?"33%":stage==="panel"?"67%":"";
  $(".portrait").classList.toggle("charging",stage==="starting");
  const detail=$("#detail");
  detail.textContent=s.text;
  detail.hidden=s.failedStep!=null || ["starting","panel","detecting"].includes(stage);
  detail.classList.toggle("problem",["busy","problem","recovery"].includes(stage));
  button("#primary",null);button("#secondary",null);
  if (idle&&!s.readOnly&&!s.installed) {
    button("#primary","Wybierz folder klienta","folder");
  } else if (idle&&!s.readOnly) {
    button("#primary","Start","start");
    if(s.clientOutdated)button("#secondary","Sprawdź aktualizacje","recheck");
  } else if(stage==="available"&&s.canInstall) {
    button("#primary","Zainstaluj aktualizację","install");
    if(s.canDefer)button("#secondary","Odłóż wydanie","defer");
  } else if(stage==="prepared") button("#primary","Połącz z grą","connect",!!s.readOnly);
  else if (["detecting","starting","panel"].includes(stage)) button("#primary","Start",null,true);
  else if (["closed","busy","problem","available","recovery"].includes(stage)||s.readOnly&&idle) button("#primary","Sprawdź ponownie","retry");
  if(stage==="waiting")button("#secondary","Anuluj","cancel");
  if(stage==="problem"&&s.canDefer&&!s.readOnly)button("#secondary","Odłóż wydanie","defer");
}

const historyDialog=$("#history");
$("#more").onclick=()=>historyDialog.showModal();
$("#closeHistory").onclick=()=>historyDialog.close();
$("#folderEdit").onclick=()=>window.desktop.act("folder");

const aiDialog=$("#ai");
let aiBusy=false, aiReadOnly=false;
const aiNotes={codex:"OpenAI Codex (aplikacja lub terminal)","claude-desktop":"Aplikacja Claude na komputer","claude-code":"Claude Code w terminalu"};
function drawAi(result) {
  const status=result.status||{}, list=$("#aiTargets");
  list.replaceChildren();
  const on=Object.values(status).filter(t=>t.connected).map(t=>t.label);
  $("#aiChip").hidden=!on.length;
  $("#aiSummary").textContent=on.length?"Zintegrowane AI: "+on.join(", "):"";
  $("#aiOpen").title=on.length?"Ustawienia · "+$("#aiSummary").textContent:"Ustawienia · integracja AI wyłączona";
  for (const [key,target] of Object.entries(status)) {
    const li=document.createElement("li"), logo=document.createElement("span"), text=document.createElement("span"),
      name=document.createElement("strong"), note=document.createElement("small"), state=document.createElement("span"), act=document.createElement("button");
    li.className=target.connected?"on":"";
    logo.className="ai-logo"+(key.startsWith("claude")?" claude":"");logo.setAttribute("aria-hidden","true");
    logo.textContent=key==="codex"?"Cx":key==="claude-code"?"CC":"C";
    name.textContent=target.label;note.textContent=aiNotes[key]||"";
    state.className="ai-state"+(target.connected?" on":target.available?"":" missing");
    state.textContent=target.connected?"Połączony":target.available?"Niepołączony":"Nie znaleziono programu";
    text.className="ai-text";text.append(name,note,state);
    act.className="ai-act"+(target.connected?" off":"");act.type="button";
    act.textContent=target.connected?"Odłącz":"Połącz";
    act.setAttribute("aria-label",act.textContent+" "+target.label);
    act.disabled=aiBusy||aiReadOnly||!target.available&&!target.connected;
    act.onclick=()=>changeAi(target.connected?"disconnect":"connect",key);
    li.append(logo,text,act);list.append(li);
  }
  if (result.message!=null) $("#aiMessage").textContent=result.message;
}
async function refreshAi() {
  try { drawAi(await window.desktop.mcp("status")); }
  catch (error) { $("#aiChip").hidden=true; $("#aiMessage").textContent=error.message; }
}
async function changeAi(operation,target) {
  if (aiBusy) return;
  aiBusy=true;$("#aiMessage").textContent="Zapisywanie…";
  try { drawAi(await window.desktop.mcp(operation,target)); }
  catch (error) { $("#aiMessage").textContent=error.message; }
  finally { aiBusy=false; refreshAi(); }
}
$("#aiOpen").onclick=()=>{$("#aiMessage").textContent="";$("#channelMessage").textContent="";aiDialog.showModal();refreshAi();};
$("#closeAi").onclick=()=>aiDialog.close();
$("#testChannel").onchange=changeChannel;
for (const dialog of [historyDialog,aiDialog]) dialog.addEventListener("click",event=>{
  if(event.target!==dialog)return;
  const rect=dialog.getBoundingClientRect();
  if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom) dialog.close();
});
window.desktop.state().then(s=>{aiReadOnly=!!s.readOnly;refreshAi();});
window.desktop.onState(update);
window.desktop.state().then(update);
