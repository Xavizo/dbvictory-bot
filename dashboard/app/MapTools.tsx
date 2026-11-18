"use client";
import type {ButtonHTMLAttributes,ReactNode} from "react";
import ActionMenu from "./ActionMenu";

const paths={
  map:'M3 5l6-2 6 2 6-2v16l-6 2-6-2-6 2V5zm6-2v16m6-14v16',
  record:'M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14',
  stop:'M6 6h12v12H6z',save:'M5 3h12l4 4v14H3V3h2zm2 0v6h10V3M7 21v-8h10v8',
  check:'M5 12l4 4L19 6',play:'M7 4l14 8-14 8V4',
  loop:'M4 9V6h13l3 3m0-3v3h-3M20 15v3H7l-3-3m0 3v-3h3',
  undo:'M9 5l-5 5 5 5M4 10h10a6 6 0 0 1 6 6',
  edit:'M4 16L16 4l4 4L8 20H4v-4M13 7l4 4',
  layers:'M12 3l10 6-10 6L2 9l10-6M2 13l10 6 10-6M2 17l10 6 10-6',
  close:'M6 6l12 12M6 18L18 6',plus:'M12 5v14M5 12h14',minus:'M5 12h14',
  up:'M5 15l7-7 7 7',down:'M5 9l7 7 7-7',
  focus:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v4m0 12v4M2 12h4m12 0h4',
  pan:'M12 2v20M2 12h20M8 6l4-4 4 4M8 18l4 4 4-4M6 8l-4 4 4 4M18 8l4 4-4 4',
  details:'M4 6h16M4 12h16M4 18h16M8 3v6m8 0v6m-6 0v6',
  optimize:'M4 20L17 7l3 3L7 23M4 3v6M1 6h6M16 1v4m-2-2h4M20 16v6m-3-3h6',
  more:'M4 11h2v2H4zM11 11h2v2h-2zM18 11h2v2h-2z',
};
export type ToolIcon=keyof typeof paths;
export function MapIcon({name}:{name:ToolIcon}){return <svg aria-hidden="true" focusable="false" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]}/></svg>;}
export function MapTool({icon,label,className="",...props}:{icon:ToolIcon;label:string}&ButtonHTMLAttributes<HTMLButtonElement>){return <button type="button" {...props} className={`map-tool ${className}`} aria-label={label} title={label}><MapIcon name={icon}/></button>;}
export function MapMore({children}:{children:ReactNode}) {
  return <ActionMenu className="map-more" triggerClass="map-tool" contentClass="map-more-menu"
    label="Więcej opcji trasy" title="Więcej opcji trasy" icon={<MapIcon name="more"/>}>{children}</ActionMenu>;
}
