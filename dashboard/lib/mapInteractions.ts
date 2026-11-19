export const activationLabels = {
  step:"Wejdź na pole", use:"Użyj obiektu", open_if_closed:"Sprawdź stan → otwórz zamknięte → przejdź",
  rope:"Rope · 3003", aio:"AiO · 8899 / 8900", shovel:"Shovel · 3457", fly_up:"Czar fly up",
  rope_or_fly_up:"Rope lub fly up",
};
export type Activation = keyof typeof activationLabels;
export const activations:Record<string,Activation[]> = {
  stairs:["step"],hole:["step"],teleport:["step"],ladder:["use"],descent:["step","use"],
  web:["aio"],door:["open_if_closed"],level_gate:["open_if_closed"],rope_spot:["rope","aio","fly_up"],covered_hole:["shovel","aio"],
};
export function defaultActivation(kind:string,saved?:string):Activation|"" {
  const allowed=activations[kind] ?? [];
  return saved && allowed.includes(saved as Activation) ? saved as Activation : allowed[0] ?? "";
}
