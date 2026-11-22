"use client";
import {useEffect,useState} from "react";
import {authenticatedFetch} from "./auth";
import type {Habitat} from "./atlasWildlife";

export type WorldZones={scope:string;revision:string;side:number;cells:[number,number,number][];habitats:Habitat[]};

export function useWorldZones(active:boolean) {
  const [zones,setZones]=useState<WorldZones|null>(null),[error,setError]=useState(""),[attempt,retry]=useState(0);
  useEffect(()=>{
    if(!active)return;
    const ctl=new AbortController();
    let revision="",loading=false;
    async function refresh(){
      if(loading||document.hidden||ctl.signal.aborted)return;
      loading=true;
      try{
        const res=await authenticatedFetch("http://127.0.0.1:8089/api/map/zones?since="+encodeURIComponent(revision),{signal:ctl.signal});
        if(!res.ok)throw Error();
        const data=await res.json();
        if(!ctl.signal.aborted){
          if(data.cells){setZones(data);revision=data.revision;}
          setError("");
        }
      }catch{if(!ctl.signal.aborted)setError("Nie udało się odświeżyć stref.");}
      finally{loading=false;}
    }
    void refresh();
    const timer=setInterval(refresh,20000);
    document.addEventListener("visibilitychange",refresh);
    return ()=>{ctl.abort();clearInterval(timer);document.removeEventListener("visibilitychange",refresh);};
  },[active,attempt]);
  return {zones,error,retry:()=>retry(v=>v+1)};
}
