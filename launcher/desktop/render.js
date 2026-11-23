"use strict";
const headings = {
  checking:"Sprawdzanie aktualizacji", applying:"Aktualizacja", ready:"Klient gry", busy:"Sesja jest zajęta",
  available:"Dostępna aktualizacja", recovery:"Aktualizacja do dokończenia", problem:"Launcher wymaga uwagi",
  preparing:"Klient gry", detecting:"Uruchamianie", clientError:"Uruchamianie", starting:"Uruchamianie",
  panel:"Uruchamianie", waiting:"Oczekiwanie", closing:"Zamykanie sesji", closed:"Sesja zakończona", running:"Panel gotowy",
  prepared:"Wybór postaci",connecting:"Łączenie z grą"
};
const checkpoints = [
  {key:"client", pending:"Klient DBVictory", active:"Wykrywanie klienta DBVictory…", done:"Wykryto klienta DBVictory"},
  {key:"engine", pending:"Silnik bota", active:"Uruchamianie silnika…", done:"Silnik gotowy"},
  {key:"panel", pending:"Główny panel", active:"Otwieranie głównego panelu…", done:"Panel gotowy"}
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

function drawSteps(s) {
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

function update(s) {
  const stage=s.phase, idle=stage==="ready"||stage==="clientError";
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
  progress.classList.toggle("indeterminate",!["starting","panel"].includes(stage));
  $(".fill").style.width=stage==="starting"?"33%":stage==="panel"?"67%":"";
  $(".portrait").classList.toggle("charging",stage==="starting");
  const detail=$("#detail");
  detail.textContent=s.text;
  detail.hidden=s.failedStep!=null || ["starting","panel","detecting"].includes(stage);
  detail.classList.toggle("problem",["busy","problem","recovery"].includes(stage));
  button("#primary",null);button("#secondary",null);
  if (idle&&!s.readOnly) {
    button("#primary","Start","start");
  } else if(stage==="prepared") button("#primary","Połącz z grą","connect");
  else if (["detecting","starting","panel"].includes(stage)) button("#primary","Start",null,true);
  else if (["closed","busy","problem","available","recovery"].includes(stage)||s.readOnly&&idle) button("#primary","Sprawdź ponownie","retry");
  if(stage==="waiting")button("#secondary","Anuluj","cancel");
  if(stage==="problem"&&s.canDefer&&!s.readOnly)button("#secondary","Odłóż wydanie","defer");
}

const historyDialog=$("#history");
$("#more").onclick=()=>historyDialog.showModal();
$("#closeHistory").onclick=()=>historyDialog.close();
historyDialog.addEventListener("click",event=>{
  if(event.target!==historyDialog)return;
  const rect=historyDialog.getBoundingClientRect();
  if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom) historyDialog.close();
});
$("#folderEdit").onclick=()=>window.desktop.act("folder");
window.desktop.onState(update);
window.desktop.state().then(update);
