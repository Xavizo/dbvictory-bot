"use client";
import { t, num } from "@/lib/i18n";
import {useEffect,useRef,useState} from 'react';
import type {CombatSpell} from '@/lib/cavebotModel';
import {catalogueCooldown,findCatalogueSpell,loadSpellCatalogue,searchSpells,selectCatalogueSpell,spellCategoryLabel,type CatalogueSpell} from '@/lib/spellCatalogue';

function useCatalogue(enabled:boolean){
  const [rows,setRows]=useState<CatalogueSpell[]>([]),[error,setError]=useState(''),[retry,setRetry]=useState(0);
  useEffect(()=>{if(!enabled)return;let cancelled=false;setError('');
    loadSpellCatalogue().then(data=>{if(!cancelled)setRows(data);}).catch(e=>{if(!cancelled)setError(e instanceof Error?e.message:String(e));});
    return()=>{cancelled=true;};
  },[enabled,retry]);
  return {rows,error,retry:()=>setRetry(n=>n+1)};
}
// Icons are exported from the player's own client; without them a symbol stands in.
export function LocalSpellIcon({src,size}:{src?:string|null;size?:number}) {
  const [failed,setFailed]=useState(false);
  useEffect(()=>setFailed(false),[src]);
  const style=size?{width:size,height:size}:undefined;
  return src&&!failed?<img className="spell-icon" src={src} style={style} alt="" loading="lazy" decoding="async" onError={()=>setFailed(true)}/>:<span className="spell-icon spell-icon-fallback" style={style} aria-hidden="true">✦</span>;
}
function SpellIcon({spell}:{spell?:CatalogueSpell}) {
  return <LocalSpellIcon src={spell?.icon}/>;
}
export default function SpellPicker({value,onChange,label,disabled=false}:{value:CombatSpell;onChange:(spell:CombatSpell)=>void;label:string;disabled?:boolean}) {
  const [open,setOpen]=useState(false),[query,setQuery]=useState(''),[limit,setLimit]=useState(20);
  const dialog=useRef<HTMLDialogElement>(null),search=useRef<HTMLInputElement>(null),trigger=useRef<HTMLButtonElement>(null);
  const catalogue=useCatalogue(open||!!value.words),selected=findCatalogueSpell(catalogue.rows,value.words);
  const results=searchSpells(catalogue.rows,query);
  function close(){setOpen(false);trigger.current?.focus();}
  useEffect(()=>{if(disabled){setOpen(false);return;}if(open){dialog.current?.showModal();search.current?.focus();}return()=>dialog.current?.close();},[open,disabled]);
  return <div className="spell-picker">
    <button type="button" ref={trigger} className="spell-picker-trigger" aria-label={label} aria-haspopup="dialog" disabled={disabled} title={selected?`${selected.name} · ${selected.words}`:value.words||t('spellPicker.chooseFromCatalogue')} onClick={()=>{setQuery('');setLimit(20);setOpen(true);}}>
      {value.words&&<SpellIcon spell={selected}/>}<span>{selected?.name||value.words||t('spellPicker.choose')+'…'}</span>{value.words&&<small className="spell-picker-change">{t('common.change')}</small>}
    </button>
    {open&&!disabled&&<dialog ref={dialog} className="spell-picker-dialog" aria-label={t("spellPicker.choose")} onCancel={e=>{e.preventDefault();e.stopPropagation();close();}} onClick={e=>{if(e.target===e.currentTarget)close();}}>
      <div className="spell-picker-content"><header><strong>{t("spellPicker.spells")} <small>{catalogue.rows.length?results.length:''}</small></strong><button type="button" className="spell-remove" aria-label={t("spellPicker.close")} onClick={close}>×</button></header>
        <input ref={search} className="spell-search" type="search" aria-label={t("spellPicker.search")} placeholder={t("spellPicker.searchPlaceholder")} value={query} onChange={e=>{setQuery(e.target.value);setLimit(20);}}/>
        <div className="spell-picker-results" aria-label={t("spellPicker.list")}>
          {catalogue.error?<p role="alert">{catalogue.error} <button type="button" onClick={catalogue.retry}>{t("common.retry")}</button></p>:!catalogue.rows.length?<p role="status">{t("spellPicker.loading")}</p>:!results.length?<p role="status">{t("spellPicker.none")}</p>:results.slice(0,limit).map(row=><button type="button" className="spell-option" key={row.id} disabled={!!row.unavailable} aria-pressed={selected?.id===row.id} title={row.unavailable||`${row.name} · ${spellCategoryLabel(row)} · ${t('spellPicker.level',{level:row.level})} · Ki ${row.ki_percent?row.ki_percent+'%':row.ki}\n${row.description}`} aria-label={`${row.name}, ${row.words}, cooldown ${row.cooldown_client_ms/1000} s`} onClick={()=>{onChange(selectCatalogueSpell(value,row));close();}}>
            <SpellIcon spell={row}/><span className="spell-option-name"><strong>{row.name}</strong><small>{row.words} · {row.unavailable?t('spellPicker.unavailable'):spellCategoryLabel(row)}</small></span><span className="spell-option-cd" title={t("spellPicker.cdTitle")}>{row.cooldown_client_ms/1000} s</span>{selected?.id===row.id&&<b aria-label={t("spellPicker.selected")}>✓</b>}
          </button>)}
          {results.length>limit&&<button type="button" className="spell-show-more" onClick={()=>setLimit(n=>n+20)}>{t("spellPicker.more")} ({Math.min(limit,results.length)}/{results.length})</button>}
        </div>
        <footer title={t("spellPicker.footerTitle")}>{t("spellPicker.footer")} <kbd>Esc</kbd></footer>
      </div>
    </dialog>}
  </div>;
}
export function SpellCooldown({value,onChange,label,disabled=false}:{value:CombatSpell;onChange:(spell:CombatSpell)=>void;label:string;disabled?:boolean}) {
  const {rows}=useCatalogue(!!value.words),entry=findCatalogueSpell(rows,value.words);
  const base=entry?catalogueCooldown(entry):null,changed=base!==null&&Math.abs(value.cooldown-base)>.00001;
  return <label className="spell-cooldown">{t('spellPicker.interval')}<span title={entry?t('spellPicker.intervalTitle',{catalogue:num(entry.cooldown_client_ms/1000),minimum:base!==entry.cooldown_client_ms/1000?' · '+t('spellPicker.minimum',{min:num(.2)}):''}):t('spellPicker.manualInterval')}>
    <input aria-label={label} type="number" min={.2} max={120} step={.1} disabled={disabled} value={value.cooldown} onChange={e=>onChange({...value,cooldown:Number(e.target.value)})}/>
    {changed&&<button type="button" disabled={disabled} aria-label={t("spellPicker.restoreAria",{base:base!})} title={t("spellPicker.restore",{base:base!})} onClick={()=>onChange({...value,cooldown:base!})}>↺</button>}
  </span></label>;
}
