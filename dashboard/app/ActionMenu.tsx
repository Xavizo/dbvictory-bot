"use client";
import {useEffect,useRef,type ReactNode} from "react";

export default function ActionMenu({className,label,title,icon,triggerClass,contentClass,children}:{
  className:string;label:string;title:string;icon:ReactNode;triggerClass?:string;contentClass?:string;children:ReactNode
}) {
  const ref=useRef<HTMLDetailsElement>(null);
  useEffect(()=>{
    const menu=ref.current;
    if (!menu) return;
    const doc=menu.ownerDocument;
    function dismiss(event:Event) {
      if (menu?.open && !event.composedPath().includes(menu)) menu.open=false;
    }
    doc.addEventListener("pointerdown",dismiss,true);
    doc.addEventListener("focusin",dismiss,true);
    return ()=>{
      doc.removeEventListener("pointerdown",dismiss,true);
      doc.removeEventListener("focusin",dismiss,true);
    };
  },[]);
  return <details ref={ref} name="panel-action-menu" className={className} onKeyDown={event=>{
    if(event.key!=="Escape" || !event.currentTarget.open)return;
    event.stopPropagation();
    event.currentTarget.open=false;
    event.currentTarget.querySelector("summary")?.focus();
  }}>
    <summary className={triggerClass} aria-label={label} title={title}>{icon}</summary>
    <div className={contentClass} onClick={event=>{
      if(event.target instanceof Element && event.target.closest("button:enabled") && ref.current)ref.current.open=false;
    }}>{children}</div>
  </details>;
}
