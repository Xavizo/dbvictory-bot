"use client";

/** A small (?) that shows its text on hover, keyboard focus or tap, so labels can stay short. */
export default function HelpTip({ text, label }: { text: string; label?: string }) {
  return <span className="help-tip" tabIndex={0} role="img" aria-label={label ? `${label}: ${text}` : text}
    data-tip={text} onClick={e => e.stopPropagation()}>?</span>;
}
