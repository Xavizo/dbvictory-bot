import { t } from "@/lib/i18n";
const activationKeys = {
  step:"activation.step", use:"activation.use", open_if_closed:"activation.open_if_closed",
  rope:"activation.rope", aio:"activation.aio", shovel:"activation.shovel", fly_up:"activation.fly_up",
  rope_or_fly_up:"activation.rope_or_fly_up",
} as const;
export type Activation = keyof typeof activationKeys;
/** How a player activates a map object, in the current language. */
export const activationLabel = (activation: Activation) => t(activationKeys[activation]);
export const activations:Record<string,Activation[]> = {
  stairs:["step"],hole:["step"],teleport:["step"],ladder:["use"],descent:["step","use"],
  web:["aio"],door:["open_if_closed"],level_gate:["open_if_closed"],rope_spot:["rope","aio","fly_up"],covered_hole:["shovel","aio"],
};
export function defaultActivation(kind:string,saved?:string):Activation|"" {
  const allowed=activations[kind] ?? [];
  return saved && allowed.includes(saved as Activation) ? saved as Activation : allowed[0] ?? "";
}
