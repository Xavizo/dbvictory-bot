"use client";

export default function ActivationCheckbox({label,checked,disabled,onChange,title}:{label:string;checked:boolean;disabled:boolean;onChange:(checked:boolean)=>void;title?:string}) {
  return <label className="section-activation" title={title}>
    <input type="checkbox" checked={checked} disabled={disabled} onChange={event=>onChange(event.target.checked)}/>
    <span>{label}</span>
  </label>;
}
