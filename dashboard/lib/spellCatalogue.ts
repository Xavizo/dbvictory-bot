import type {CombatSpell} from './cavebotModel';

export type CatalogueSpell={id:number;name:string;words:string;description:string;category:string;icon:string|null;cooldown_client_ms:number;level:number;ki:number;ki_percent:number;aggressive:boolean;unavailable:string|null};
export const normalizeSpellWords=(words:string)=>words.trim().toLowerCase();
export function parseSpellCatalogue(value:unknown):CatalogueSpell[]{
  const rows=(value as {spells?:unknown})?.spells;
  if(!Array.isArray(rows)||rows.length>3000)throw Error('Nieprawidłowy katalog czarów.');
  const ids=new Set<number>(),words=new Set<string>();
  return rows.map(row=>{
    if(!row||typeof row!=='object'||![row.id,row.cooldown_client_ms,row.level,row.ki,row.ki_percent].every(n=>typeof n==='number'&&Number.isFinite(n)&&n>=0)||!Number.isInteger(row.id)||
      !['name','words','description','category'].every(k=>typeof row[k]==='string')||typeof row.aggressive!=='boolean'||
      !(row.unavailable===null||typeof row.unavailable==='string')||
      !(row.icon===null||typeof row.icon==='string'&&/^\/local-spells\/icons\/\d+\.png$/.test(row.icon))||
      ids.has(row.id)||words.has(normalizeSpellWords(row.words)))throw Error('Nieprawidłowy wpis w katalogu czarów.');
    if(!row.unavailable&&!/^[a-z][a-z '-]{0,63}$/i.test(row.words))throw Error('Nieprawidłowe słowa czaru.');
    ids.add(row.id);words.add(normalizeSpellWords(row.words));
    return {...row,words:normalizeSpellWords(row.words)} as CatalogueSpell;
  });
}
export function findCatalogueSpell(rows:CatalogueSpell[],words:string){return rows.find(row=>row.words===normalizeSpellWords(words));}
const categoryLabels:Record<string,string>={Wave:'Fala',Distanse:'Dystans',Area:'Obszar',Area_one:'Wybrane pole',Boost:'Wzmocnienie',Heal:'Leczenie',Useful:'Użytkowy',Transform:'Transformacja',Revert:'Powrót formy',barrier:'Bariera',speed:'Przyspieszenie',light:'Światło',Clone:'Klon',sense:'Wykrywanie',trap:'Pułapka',Ghost:'Duch',autodestexplo:'Eksplozja',createitem:'Tworzenie przedmiotu',createsenzu:'Tworzenie Senzu',usefull_target:'Wsparcie celu',monster:'Narzędzie'};
// User-confirmed on-target behavior; the client asset category says Wave.
// Match combat_wave.TARGETED_SPELLS without changing the source icon or CD.
export const spellCategoryLabel=(row:CatalogueSpell)=>normalizeSpellWords(row.words)==='shinretsuha'?'Celowany':categoryLabels[row.category]??row.category;
export function searchSpells(rows:CatalogueSpell[],query:string){
  const terms=query.toLocaleLowerCase('pl').trim().split(/\s+/).filter(Boolean);
  return rows.filter(row=>terms.every(term=>`${row.name} ${row.words} ${row.category} ${spellCategoryLabel(row)} ${row.id}`.toLocaleLowerCase('pl').includes(term)));
}
export const catalogueCooldown=(row:CatalogueSpell)=>Math.max(.2,Math.min(120,row.cooldown_client_ms/1000));
export function selectCatalogueSpell(current:CombatSpell,row:CatalogueSpell):CombatSpell {
  if(row.unavailable)throw Error(row.unavailable);
  // Reopening/reselecting the current spell must not erase a manual CD override.
  return normalizeSpellWords(current.words)===row.words?current:{...current,words:row.words,cooldown:catalogueCooldown(row),aim:row.category==='Area'?'self':'target'};
}
let cache:Promise<CatalogueSpell[]>|null=null;
export function loadSpellCatalogue():Promise<CatalogueSpell[]> {
  if(!cache)cache=fetch('/local-spells/catalog.json').then(async response=>{
    if(!response.ok)throw Error('Katalog czarów jest niedostępny.');
    return parseSpellCatalogue(await response.json());
  }).catch(error=>{cache=null;throw error;});
  return cache;
}
