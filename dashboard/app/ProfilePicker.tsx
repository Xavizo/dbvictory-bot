"use client";
import { t } from "@/lib/i18n";
import Help from "@/app/Help";
import {useEffect,useState} from "react";
import type {BotState} from "@/lib/api";
import {authenticatedFetch} from "@/lib/auth";
import ActionMenu from "./ActionMenu";
type Row={id:string;name:string;kind:"combat"|"bot";config:unknown};
async function request(body?:unknown){const response=await authenticatedFetch('http://127.0.0.1:8089/api/profiles',body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);const value=await response.json();if(!response.ok)throw Error(value.error??t('profile.error'));return value;}
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
      await refresh();setId(result.message.id);setName(result.message.name);setNote(t('profile.saved'));
      if(kind==='combat'){const loaded=await request({command:'apply',kind,id:result.message.id});onApplied?.(loaded.message.config);await onRefresh();}
    }else if(command==='apply'){
      const result=await request({command,kind,id:target});setId(target);onApplied?.(result.message.config);await onRefresh();setNote(t(kind==='bot'?'profile.botLoaded':'profile.combatLoaded'));
    }else {await request({command,kind,id:target});await refresh();setId('');setName('');await onRefresh();}
    setSaveOpen(false);
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  async function bind(target:string|null){setBusy(true);setError('');try{
    await request({command:'bind',kind:'combat',id:target,scope,draft_id:routeId});await onRefresh();
    setNote(t(target?'profile.bound':'profile.unbound'));
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  const compactCombat=<section className="combat-profile-bar" aria-label={t("profile.combatAria")}>
    <div className="combat-profile-row">
      <span>{t("profile.profile")}</span>
      <select aria-label={t("profile.chooseCombat")} title={t(dirty?'profile.dirtyTitle':'profile.chooseTitle')} value={id} disabled={busy||cannotApply}
        onChange={e=>{const next=e.target.value;if(next==='legacy'){onImport?.(legacy);setId('');setName('');setNote(t('profile.fromDraft'));return;}if(next)void act('apply',next);else{setId('');setName('');setNote('');}}}>
        <option value="">{t("profile.current")}</option>{rows.filter(r=>r.kind===kind).map(r=><option value={r.id} key={r.id}>{r.name}</option>)}
        {!!legacy&&<option value="legacy">{t("profile.importLegacy")}</option>}
      </select>
      <button className="profile-icon" aria-label={t("profile.saveCombat")} title={t("profile.saveAsProfile")} aria-expanded={saveOpen} disabled={busy||disabled} onClick={()=>setSaveOpen(v=>!v)}>
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M4 3h13l4 4v14H3V3h1zm3 0v7h10V3M7 21v-7h10v7"/></svg>
      </button>
      <ActionMenu className="profile-more" label={t("profile.options")} title={t("profile.optionsTitle")} icon="⋯">
        {routeId&&scope&&<><span>{state?.profile_selection?.routes?.[`${scope}:${routeId}`]?t('profile.routeHas'):t('profile.routeHasNot')}</span>
          <button disabled={busy||disabled||dirty||!id} onClick={()=>void bind(id)}>{t("profile.bind")}</button>
          {!!state?.profile_selection?.routes?.[`${scope}:${routeId}`]&&<button disabled={busy||disabled} onClick={()=>void bind(null)}>{t("profile.unbind")}</button>}</>}
        <button disabled={!id||busy||disabled} onClick={()=>void act('delete')}>{t("profile.delete")}</button>
      </ActionMenu>
    </div>
    {saveOpen&&<div className="combat-profile-save" role="group" aria-label={t("profile.saveGroup")}>
      <input aria-label={t("profile.combatName")} placeholder={t("profile.name")} maxLength={60} value={name} disabled={busy} onChange={e=>setName(e.target.value)}/>
      <button disabled={busy||disabled||!name.trim()} onClick={()=>void act('save','')}>{t("profile.saveNew")}</button>
      {id&&<button disabled={busy||disabled||!name.trim()} onClick={()=>void act('save')}>{t("profile.overwrite")}</button>}
      <button aria-label={t("profile.cancelSave")} className="profile-dismiss" onClick={()=>setSaveOpen(false)}>×</button>
    </div>}
    {error&&<p role="alert" className="text-red-300">{error}</p>}{note&&<p role="status" className="profile-note">{note}</p>}
  </section>;
  const content=<><div className="profile-controls">
    <label>{t(kind==='combat'?'profile.combat':'profile.bot')}<select aria-label={t(kind==='combat'?'profile.chooseCombat':'profile.chooseBot')} disabled={busy||cannotApply} value={id} onChange={e=>{if(e.target.value==='legacy'){onImport?.(legacy);return;}setId(e.target.value);}}>
      <option value="">{t("profile.currentOrNew")}</option>{rows.filter(r=>r.kind===kind).map(r=><option value={r.id} key={r.id}>{r.name}</option>)}{kind==='combat'&&!!legacy&&<option value="legacy">{t("profile.legacyRoute")}</option>}
    </select></label>
    {id&&<button disabled={busy||cannotApply} onClick={()=>void act('apply')}>{t("profile.load")}</button>}
    <label>{t("profile.nameLabel")}<input aria-label={t(kind==='combat'?'profile.combatName':'profile.botName')} maxLength={60} value={name} disabled={busy||disabled} onChange={e=>setName(e.target.value)}/></label>
    <button disabled={busy||disabled||!name.trim()} onClick={()=>void act('save')}>{t("profile.save")}</button>
    {id&&<button title={t("profile.saveNewTitle")} aria-label={t("profile.saveCopy")} disabled={busy||disabled||!name.trim()} onClick={()=>void act('save','')}>＋</button>}
    {id&&<button title={t("profile.delete")} aria-label={t("profile.delete")} disabled={busy||disabled} onClick={()=>void act('delete')}>×</button>}
  </div>{kind==='combat'&&routeId&&scope&&<div className="profile-binding"><span>{state?.profile_selection?.routes?.[`${scope}:${routeId}`]?t('profile.routeHasCombat'):t('profile.routeNoCombat')}</span><button disabled={busy||disabled||dirty||!id} onClick={async()=>{setBusy(true);setError('');try{await request({command:'bind',kind:'combat',id,scope,draft_id:routeId});await onRefresh();setNote(t('profile.remembered'));}catch(e){setError(String(e));}finally{setBusy(false);}}}>{t("profile.bindShort")}</button>{state?.profile_selection?.routes?.[`${scope}:${routeId}`]&&<button aria-label={t("profile.unbindAria")} disabled={busy||disabled} onClick={async()=>{try{await request({command:'bind',kind:'combat',id:null,scope,draft_id:routeId});await onRefresh();}catch(e){setError(String(e));}}}>×</button>}</div>}{error&&<p role="alert" className="text-red-300">{error}</p>}{note&&<p role="status" className="profile-note">{note}</p>}</>;
  return kind==='bot'?<details className="profile-panel"><summary>{t("profile.botProfiles")}<Help text={t("profile.botNote")}/></summary>{content}</details>:compactCombat;
}
