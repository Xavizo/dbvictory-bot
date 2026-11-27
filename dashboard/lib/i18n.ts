// The panel's words in English and Polish: lib/i18n/pl.json and lib/i18n/en.json.
// The language is the launcher's choice; the panel state carries it (see useBot) and
// setLanguage() switches every later t() call. Polish is the default.
import pl from "./i18n/pl.json";
import en from "./i18n/en.json";

export type Language = "pl" | "en";
export type TextKey = keyof typeof pl;
type Params = Record<string, string | number>;

const dictionaries: Record<Language, Record<string, string>> = { pl, en };
const locales: Record<Language, string> = { pl: "pl-PL", en: "en-GB" };
let current: Language = "pl";
const listeners = new Set<(language: Language) => void>();

export const isLanguage = (value: unknown): value is Language => value === "pl" || value === "en";
export const language = () => current;
export const locale = () => locales[current];

/** Switch the panel's words; returns whether anything changed. */
export function setLanguage(next: unknown): boolean {
  if (!isLanguage(next) || next === current) return false;
  current = next;
  if (typeof document !== "undefined") document.documentElement.lang = next;
  for (const listener of listeners) listener(next);
  return true;
}
export function onLanguage(listener: (language: Language) => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function fill(text: string, params?: Params) {
  return params ? text.replace(/\{(\w+)\}/g, (match, name) => name in params ? String(params[name]) : match) : text;
}

/** The text for a key in the current language; Polish fills a key English misses. */
export function t(key: TextKey, params?: Params): string {
  return fill(dictionaries[current][key] ?? pl[key] ?? key, params);
}

/** A count with its noun: keys `<key>.one`, `.few`, `.many`, `.other` (Polish) or `.one`, `.other` (English). */
export function plural(key: string, count: number, params?: Params): string {
  const form = new Intl.PluralRules(locales[current]).select(count);
  const words = dictionaries[current];
  const text = words[`${key}.${form}`] ?? words[`${key}.other`] ?? words[`${key}.many`] ?? key;
  return fill(text, { n: count, ...params });
}

/** A number in the current language's notation (1 234,5 or 1,234.5). */
export function num(value: number, options?: Intl.NumberFormatOptions): string {
  return value.toLocaleString(locales[current], options);
}
