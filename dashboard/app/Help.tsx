"use client";
import {useId, useLayoutEffect, useRef, useState} from "react";

/** A small "?" next to a control. Hover, focus or tap shows the explanation, so the control itself keeps a short label. */
export default function Help({text}: {text: string}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{left: number; top: number} | null>(null);
  const anchor = useRef<HTMLSpanElement>(null);
  const tip = useRef<HTMLSpanElement>(null);
  const id = useId();
  useLayoutEffect(() => {
    if (!open || !anchor.current || !tip.current) return;
    const a = anchor.current.getBoundingClientRect(), b = tip.current.getBoundingClientRect();
    const margin = 8, vw = window.innerWidth, vh = window.innerHeight;
    const left = Math.min(Math.max(margin, a.left + a.width / 2 - b.width / 2), vw - b.width - margin);
    const below = a.bottom + 6;
    const top = below + b.height > vh - margin && a.top - 6 - b.height > margin ? a.top - 6 - b.height : below;
    setPos({left, top});
  }, [open, text]);
  useLayoutEffect(() => {
    if (!open) return;
    const close = (e: Event) => { if (e.type !== "pointerdown" || !anchor.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("scroll", close, true);
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("scroll", close, true); window.removeEventListener("pointerdown", close); window.removeEventListener("keydown", key); };
  }, [open]);
  return <span ref={anchor} className="help-tip" tabIndex={0} role="button" aria-label={text} aria-describedby={open ? id : undefined}
    aria-expanded={open} onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)} onFocus={() => setOpen(true)}
    onBlur={() => setOpen(false)} onClick={e => { e.preventDefault(); e.stopPropagation(); setOpen(true); }}>
    ?
    {open && <span ref={tip} id={id} role="tooltip" className="help-tip-text"
      style={pos ? {left: pos.left, top: pos.top} : {visibility: "hidden", left: 0, top: 0}}>{text}</span>}
  </span>;
}
