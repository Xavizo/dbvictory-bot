// Walka (standalone combat) and the shared combat editor, rendered without a bot.
const test = require("node:test");
const assert = require("node:assert/strict");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { clampInt, defaultStandaloneCombat, SCREEN_RANGE } = require("@/lib/cavebotModel");
const Panel = require("@/app/StandaloneCombatPanel").default;

const action = (extra = {}) => ({ name: "auto_combat", description: "", enabled: true, running: true, completed: false, logs: [],
  config: { combat: { ...defaultStandaloneCombat(), hold_target: true, rules: [{ pattern: "Mutant Wolf", action: "attack", priority: 80 }] } }, ...extra });
const render = state => renderToStaticMarkup(React.createElement(Panel, { state, onRefresh: () => {} }));

test("number fields stay in range and keep the old value when emptied", () => {
  assert.equal(SCREEN_RANGE, 8);
  assert.equal(clampInt("8", 1, SCREEN_RANGE), 8);   // full screen distance no longer fails at Start
  assert.equal(clampInt("12", 1, SCREEN_RANGE), 8);
  assert.equal(clampInt("0", 1, 7), 1);
  assert.equal(clampInt("", 1, 7, 5), 5);
  assert.equal(clampInt("2.6", 0, 100), 3);
});

test("Walka shows its own Techniki, Potwory and Hold target, and says when the cavebot has priority", () => {
  const html = render({ connected: true, actions: [action()], standalone_combat: { status: "yielding", reason: "" } });
  assert.match(html, /Walka/);
  assert.match(html, /Techniki/);
  assert.doesNotMatch(html, /Wspólne czary/);
  assert.match(html, /Potwory/);
  assert.match(html, /Trzymaj cel/);
  assert.match(html, /Czeka · działa cavebot/);
  assert.match(html, /<option value="visible" selected="">Gdy potwór jest na ekranie/);
  assert.match(html, /class="help-tip"/);
});

test("Walka names the held target while it is away", () => {
  const html = render({ connected: true, actions: [action()],
    standalone_combat: { status: "fighting", reason: "", combat: { held: { id: 1, name: "Mutant Wolf", lost: true } } } });
  assert.match(html, /Działa · czekam na Mutant Wolf/);
});

test("without the action there is no panel", () => {
  assert.equal(render({ connected: true, actions: [] }), "");
});
