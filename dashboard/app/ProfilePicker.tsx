"use client";
import {useEffect,useState} from "react";
import type {BotState} from "@/lib/api";
import {authenticatedFetch} from "@/lib/auth";
type Row={id:string;name:string;kind:"combat"|"bot";config:unknown};
async function request(body?:unknown){const response=await authenticatedFetch('http://127.0.0.1:8089/api/profiles',body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);const value=await response.json();if(!response.ok)throw Error(value.error??'Błąd profilu');return value;}
export default function ProfilePicker({kind,state,config,disabled=false,dirty=false,onApplied,onRefresh,legacy,onImport,routeId,scope}:{kind:"combat"|"bot";state:BotState|null;config?:unknown;disabled?:boolean;dirty?:boolean;onApplied?:(value:unknown)=>void;onRefresh:()=>void;routeId?:string;scope?:string;legacy?:unknown;onImport?:(value:unknown)=>void}){
  const [rows,setRows]=useState<Row[]>([]),[id,setId]=useState(''),[name,setName]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[note,setNote]=useState('');
  const [saveOpen,setSaveOpen]=useState(false);
  const selected=state?.profile_selection?.[kind];
  async function refresh(){const value=await request();setRows(value.profiles);return value.profiles as Row[];}
  useEffect(()=>{void refresh().catch(e=>setError(String(e)));},[]);
  useEffect(()=>setId(selected??''),[selected]);
  useEffect(()=>setName(rows.find(r=>r.id===id)?.name??''),[id,rows]);
  const active=state?.actions.some(a=>a.running&&a.name!=='dll_bridge');
  const cannotApply=disabled||dirty||(kind==='bot'&&active);
  async function act(command:"save"|"apply"|"delete",target=id){setBusy(true);setError('');setNote('');try{
    if(command==='save'){
      const result=await request({command,kind,name,config,id:target||undefined});
      await refresh();setId(result.message.id);setName(result.message.name);setNote('Profil zapisany.');
      if(kind==='combat'){const loaded=await request({command:'apply',kind,id:result.message.id});onApplied?.(loaded.message.config);await onRefresh();}
    }else if(command==='apply'){
      const result=await request({command,kind,id:target});setId(target);onApplied?.(result.message.config);await onRefresh();setNote(kind==='bot'?'Wczytano ustawienia. Akcje pozostają wyłączone.':'Profil walki wczytany.');
    }else {await request({command,kind,id:target});await refresh();setId('');setName('');await onRefresh();}
    setSaveOpen(false);
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  async function bind(target:string|null){setBusy(true);setError('');try{
    await request({command:'bind',kind:'combat',id:target,scope,draft_id:routeId});await onRefresh();
    setNote(target?'Profil przypisany do trasy.':'Usunięto powiązanie z trasą.');
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  const compactCombat=<section className="combat-profile-bar" aria-label="Niezależny profil walki">
    <div className="combat-profile-row">
      <span>Profil</span>
      <select aria-label="Wybierz profil walki" title={dirty?'Zapisz lub odrzuć ustawienia walki przed wczytaniem innego profilu.':'Wybór wczytuje ustawienia, bez uruchamiania walki.'} value={id} disabled={busy||cannotApply}
        onChange={e=>{const next=e.target.value;if(next==='legacy'){onImport?.(legacy);setId('');setName('');setNote('Wczytano ze szkicu do edycji.');return;}if(next)void act('apply',next);else{setId('');setName('');setNote('');}}}>
        <option value="">Bieżące ustawienia</option>{rows.filter(r=>r.kind===kind).map(r=><option value={r.id} key={r.id}>{r.name}</option>)}
        {!!legacy&&<option value="legacy">Importuj dawną walkę ze szkicu…</option>}
      </select>
      <button className="profile-icon" aria-label="Zapisz profil walki" title="Zapisz ustawienia jako profil" aria-expanded={saveOpen} disabled={busy||disabled} onClick={()=>setSaveOpen(v=>!v)}>
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M4 3h13l4 4v14H3V3h1zm3 0v7h10V3M7 21v-7h10v7"/></svg>
      </button>
      <details className="profile-more"><summary aria-label="Opcje profilu walki" title="Przypisanie i usuwanie profilu">⋯</summary><div>
        {routeId&&scope&&<><span>{state?.profile_selection?.routes?.[`${scope}:${routeId}`]?'Trasa ma przypisany profil.':'Trasa bez przypisanego profilu.'}</span>
          <button disabled={busy||disabled||dirty||!id} onClick={()=>void bind(id)}>Przypisz profil do trasy</button>
          {!!state?.profile_selection?.routes?.[`${scope}:${routeId}`]&&<button disabled={busy||disabled} onClick={()=>void bind(null)}>Odłącz profil od trasy</button>}</>}
        <button disabled={!id||busy||disabled} onClick={()=>void act('delete')}>Usuń profil</button>
      </div></details>
    </div>
    {saveOpen&&<div className="combat-profile-save" role="group" aria-label="Zapis profilu walki">
      <input aria-label="Nazwa profilu walki" placeholder="Nazwa profilu" maxLength={60} value={name} disabled={busy} onChange={e=>setName(e.target.value)}/>
      <button disabled={busy||disabled||!name.trim()} onClick={()=>void act('save','')}>Zapisz jako nowy</button>
      {id&&<button disabled={busy||disabled||!name.trim()} onClick={()=>void act('save')}>Nadpisz profil</button>}
      <button aria-label="Anuluj zapis profilu" className="profile-dismiss" onClick={()=>setSaveOpen(false)}>×</button>
    </div>}
    {error&&<p role="alert" className="text-red-300">{error}</p>}{note&&<p role="status" className="profile-note">{note}</p>}
  </section>;
  const content=<><div className="profile-controls">
    <label>{kind==='combat'?'Profil walki':'Profil bota'}<select aria-label={kind==='combat'?'Wybierz profil walki':'Wybierz profil bota'} disabled={busy||cannotApply} value={id} onChange={e=>{if(e.target.value==='legacy'){onImport?.(legacy);return;}setId(e.target.value);}}>
      <option value="">Bieżące ustawienia / nowy profil</option>{rows.filter(r=>r.kind===kind).map(r=><option value={r.id} key={r.id}>{r.name}</option>)}{kind==='combat'&&!!legacy&&<option value="legacy">Dawna walka z tej trasy</option>}
    </select></label>
    {id&&<button disabled={busy||cannotApply} onClick={()=>void act('apply')}>Wczytaj</button>}
    <label>Nazwa<input aria-label={kind==='combat'?'Nazwa profilu walki':'Nazwa profilu bota'} maxLength={60} value={name} disabled={busy||disabled} onChange={e=>setName(e.target.value)}/></label>
    <button disabled={busy||disabled||!name.trim()} onClick={()=>void act('save')}>Zapisz profil</button>
    {id&&<button title="Zapisz jako nowy profil" aria-label="Zapisz kopię profilu" disabled={busy||disabled||!name.trim()} onClick={()=>void act('save','')}>＋</button>}
    {id&&<button title="Usuń profil" aria-label="Usuń profil" disabled={busy||disabled} onClick={()=>void act('delete')}>×</button>}
  </div>{kind==='combat'&&routeId&&scope&&<div className="profile-binding"><span>{state?.profile_selection?.routes?.[`${scope}:${routeId}`]?'Trasa ma przypisany profil walki.':'Trasa bez domyślnego profilu walki.'}</span><button disabled={busy||disabled||dirty||!id} onClick={async()=>{setBusy(true);setError('');try{await request({command:'bind',kind:'combat',id,scope,draft_id:routeId});await onRefresh();setNote('Zapamiętano profil dla tej trasy.');}catch(e){setError(String(e));}finally{setBusy(false);}}}>Przypisz do trasy</button>{state?.profile_selection?.routes?.[`${scope}:${routeId}`]&&<button aria-label="Usuń powiązanie walki z trasą" disabled={busy||disabled} onClick={async()=>{try{await request({command:'bind',kind:'combat',id:null,scope,draft_id:routeId});await onRefresh();}catch(e){setError(String(e));}}}>×</button>}</div>}{kind==='bot'&&<small>Ustawienia leczenia, banda, Speed Up, alertów i walki. Wczytywanie wymaga zatrzymanych akcji.</small>}{error&&<p role="alert" className="text-red-300">{error}</p>}{note&&<p role="status" className="profile-note">{note}</p>}</>;
  return kind==='bot'?<details className="profile-panel"><summary>Profile bota</summary>{content}</details>:compactCombat;
}
