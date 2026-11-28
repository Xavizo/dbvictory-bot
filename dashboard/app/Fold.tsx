"use client";
import {useState, type MouseEvent} from "react";
import {t} from "@/lib/i18n";

/** Open/closed state of an Automatyka section. Sections start collapsed; a click on the heading
 *  (outside its switches, fields and "?") or on the arrow opens it. */
export function useFold(label: string) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen(o => !o);
  const onHeading = (e: MouseEvent) => {
    if ((e.target as Element).closest("button,input,select,label,a,.help-tip,[role=switch]")) return;
    toggle();
  };
  const arrow = <button type="button" className="fold-toggle" aria-expanded={open}
    aria-label={t(open ? "common.collapse" : "common.expand", {name: label})} onClick={toggle}><span aria-hidden="true">▸</span></button>;
  return {open, arrow, onHeading};
}
