"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const os = require("node:os");
const fs = require("node:fs/promises");
const test = require("node:test");
const {history, windowPid} = require("../launcher/desktop/host");

test("a clean installation gets readable notes up to its installed version", async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "dbv-notes-"));
  t.after(() => fs.rm(root, {recursive:true, force:true}));
  assert.deepEqual((await history(root, "v1.2.0 (example)")).map(r => r.version), ["v1.2.0", "v1.1.0"]);
  const directory = path.join(root, "launcher", "desktop");
  await fs.mkdir(directory, {recursive:true});
  const file = path.join(directory, "release-notes.json");
  const notes = ["v1.10.0", "v1.9.0", "v1.8.9"].map(version => ({version, changes:[{title:"Zmiana",detail:"Opis"}]}));
  await fs.writeFile(file, JSON.stringify(notes));
  assert.deepEqual(await history(root, "v1.9.0 (abcdef0)"), notes.slice(1));
  await fs.writeFile(file, "invalid");
  await assert.rejects(history(root, "v1.9.0"), SyntaxError);
});

test("client detection requires a window in the selected installation", () => {
  const folder = path.resolve("DBVictory");
  const proc = {Id:4, Path:path.join(folder, "dbvStart.exe"), HasWindow:true};
  const other = {...proc, Id:7, Path:path.join(folder, "other", "dbvStart_gl.exe")};
  assert.equal(windowPid([other, {...proc, HasWindow:false}], folder), null);
  assert.equal(windowPid([other, proc], folder.toUpperCase()), 4);
  assert.throws(() => windowPid([proc, {...proc, Id:8}], folder), {code:"MULTIPLE_CLIENTS"});
});
