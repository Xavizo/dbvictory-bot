"use strict";
const headings={checking:"Sprawdzanie aktualizacji",applying:"Aktualizacja",ready:"Klient gry",busy:"Sesja jest zajęta",
  available:"Dostępna aktualizacja",recovery:"Aktualizacja do dokończenia",
  problem:"Launcher wymaga uwagi",opening:"Przygotowywanie",confirm:"Lista postaci",starting:"Uruchamianie bota",
  panel:"Otwieranie panelu",waiting:"Oczekiwanie na panel",closing:"Zamykanie sesji",running:"Panel gotowy"};
let current, lastChanges;
function button(id,label,action) {
  const el=$(id);el.hidden=!label;el.textContent=label||"";el.onclick=action?()=>window.desktop.act(action):null;
}
function fillChanges(items) {
  const encoded=JSON.stringify(items);
  if(lastChanges===encoded)return;
  lastChanges=encoded;const list=$("#changes");list.replaceChildren();
  for (const [i,name] of items.slice(0,5).entries()){
    const li=document.createElement("li"),orb=document.createElement("span"),summary=document.createElement("span");
    orb.className="orb";orb.setAttribute("aria-hidden","true");
    for(const [x,y] of layouts[i]){const star=document.createElement("i");star.className="star";star.style.left=x+"%";star.style.top=y+"%";orb.append(star);}
    summary.className="summary";summary.title=name;summary.textContent=name;li.append(orb,summary);list.append(li);
  }
}
function update(s) {
  current=s;const stage=s.phase,ready=stage==="ready";
  $("#version").textContent=s.version||"Sprawdzanie wersji…";$("#heading").textContent=headings[stage];
  $("#detail").textContent=s.text;fillChanges(s.changes);
  $("#folderRow").hidden=!ready;$("#folderPath").textContent=s.folder;$("#folderPath").title=s.folder;$("#folderEdit").hidden=s.readOnly;
  const badge=$("#badge");badge.hidden=!ready;badge.classList.toggle("absent",!s.clientOpen);
  badge.textContent=s.clientOpen?"Uruchomiony":s.installed?"Wykryty":"Wybierz folder";
  const index=["starting","panel","running"].indexOf(stage);
  const progress=$("#progress");progress.hidden=index<0&&!["checking","applying","opening","waiting"].includes(stage);
  progress.classList.toggle("indeterminate",index<0);
  $(".fill").style.width=index<0?"":(index*50)+"%";
  const steps=$("#steps");steps.replaceChildren();
  if(index>=0)for(const [i,name] of ["Silnik","Główne okno"].entries()){
    const li=document.createElement("li"),icon=document.createElement("i");
    li.className=i<index?"done":i===index?"active":"";icon.className="stepicon";icon.textContent=i<index?"✓":"";
    li.append(icon,document.createTextNode(name));steps.append(li);
  }
  $(".portrait").classList.toggle("charging",stage==="starting");
  button("#primary",null);button("#secondary",null);
  if(ready&&!s.readOnly) button("#primary",s.installed?"▶  Uruchom bota":"Wybierz folder",s.installed?"start":"folder");
  else if(stage==="confirm")button("#primary","Jestem na liście postaci","confirmed");
  else if(["busy","problem","available","recovery"].includes(stage)||s.readOnly&&ready)button("#primary","Sprawdź ponownie","retry");
  if(stage==="waiting"||stage==="confirm")button("#secondary","Anuluj","cancel");
  if(stage==="problem"&&s.canDefer&&!s.readOnly)button("#secondary","Odłóż wydanie","defer");
}
$("#folderEdit").onclick=()=>window.desktop.act("folder");
document.addEventListener("keydown",event=>{
  if(event.key==="Enter"&&!event.repeat&&current?.phase==="confirm"&&!event.target.closest("button")){
    event.preventDefault();window.desktop.act("confirmed");
  }
});
window.desktop.onState(update);
window.desktop.state().then(update);
