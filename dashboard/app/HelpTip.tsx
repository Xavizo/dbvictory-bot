"use client";
import Help from "./Help";

/** A small (?) next to a label: the shared Help tooltip, so every (?) in the panel looks and behaves the same. */
export default function HelpTip({ text, label }: { text: string; label?: string }) {
  return <Help text={label ? `${label}: ${text}` : text}/>;
}
