"use client";
import { t } from "@/lib/i18n";
import {useState} from "react";
import type {DraftTag} from "@/lib/cavebotModel";

const palette = ["mint", "blue", "amber", "rose", "violet"] as const;
type Edit = {index:number; tag:DraftTag; original:DraftTag[]};

export default function DraftTags({tags, onSave}:{tags:DraftTag[]; onSave:(next:DraftTag[], previous:DraftTag[])=>Promise<void>}) {
  const [edit,setEdit] = useState<Edit|null>(null);
  const [error,setError] = useState("");
  const [saving,setSaving] = useState(false);
  function open(index:number) {
    setError("");
    setEdit({index, tag:tags[index] ?? {text:"",color:"mint"}, original:tags});
  }
  async function save(remove=false) {
    if (!edit || saving) return;
    const next = edit.original.slice();
    if (remove) next.splice(edit.index,1);
    else next[edit.index] = {...edit.tag, text:edit.tag.text.trim()};
    setSaving(true);
    try { await onSave(next, edit.original); setEdit(null); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  }
  return <section className="draft-tags" aria-label={t("tags.aria")}>
    <div className="draft-tag-list">
      {tags.map((tag,i)=><button type="button" className="draft-tag" data-color={tag.color} key={i} disabled={saving} aria-label={t("tags.edit",{text:tag.text})} onClick={()=>open(i)}>{tag.text}</button>)}
      {!edit && tags.length<16 && <button type="button" className="draft-tag-add" onClick={()=>open(tags.length)}>+ {t("tags.add")}</button>}
    </div>
    {edit && <form className="draft-tag-editor" onSubmit={e=>{e.preventDefault();void save();}}>
      <input autoFocus aria-label={t("tags.text")} placeholder={t("tags.placeholder")} maxLength={80} value={edit.tag.text} disabled={saving}
        onChange={e=>setEdit({...edit,tag:{...edit.tag,text:e.target.value}})}/>
      <div className="draft-tag-colors" role="group" aria-label={t("tags.color")}>
        {(palette as readonly DraftTag["color"][]).map(color=><button type="button" className="draft-tag-swatch" key={color} data-color={color}
          aria-label={t(`tags.color.${color}`)} title={t(`tags.color.${color}`)} aria-pressed={edit.tag.color===color} disabled={saving}
          onClick={()=>setEdit({...edit,tag:{...edit.tag,color}})}>{edit.tag.color===color?"✓":null}</button>)}
      </div>
      <button disabled={saving||!edit.tag.text.trim()}>{t("tags.confirm")}</button>
      <button type="button" disabled={saving} onClick={()=>{setEdit(null);setError("");}}>{t("common.cancel")}</button>
      {edit.index<edit.original.length && <button type="button" className="draft-tag-remove" disabled={saving} onClick={()=>void save(true)}>{t("common.delete")}</button>}
    </form>}
    {error && <small role="alert">{error}</small>}
  </section>;
}
