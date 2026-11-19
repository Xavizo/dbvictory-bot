"use strict";
const { execFile, spawn } = require("node:child_process");
const { promisify } = require("node:util");
const fs = require("node:fs/promises");
const path = require("node:path");
const readline = require("node:readline");
const exec = promisify(execFile);
const options = { windowsHide:true, encoding:"utf8", maxBuffer:256*1024 };
const validSha = value => typeof value === "string" && /^[a-f0-9]{40}$/.test(value);
const clientNames = ["dbvStart.exe", "dbvStart_gl.exe"];

async function checkRelease(root, args) {
  const python = path.join(root, "venv", "Scripts", "python.exe");
  let output;
  try {
    output = (await exec(python, ["-B","-X","utf8","launcher_files.py",...args], {...options,cwd:root})).stdout;
  } catch (error) {
    if (!error.stdout) throw new Error("Nie udało się wykonać lokalnego aktualizatora. Sprawdź instalację Pythona.");
    output = error.stdout;
    if (JSON.parse(output).state !== "error") throw new Error("Aktualizator zakończył się błędem.");
  }
  const report = JSON.parse(output);
  if (!["current","updated","available","recovery","busy","error"].includes(report.state) ||
      (report.installed != null && !validSha(report.installed)) || (report.pending != null && !validSha(report.pending)) ||
      typeof report.message !== "string") throw new Error("Niepoprawna odpowiedź aktualizatora.");
  return report;
}

async function history(root, label) {
  const match = /^v(\d+)\.(\d+)\.(\d+)(?:\s|$)/.exec(label || "");
  if (!match) return [];
  const installed = match.slice(1).map(Number);
  const file = path.join(root,"launcher","desktop","release-notes.json");
  let notes;
  try { notes = JSON.parse(await fs.readFile(file,"utf8")); }
  catch (error) {
    if (error.code !== "ENOENT") throw error;
    notes = require("./release-notes.json");
  }
  if (!Array.isArray(notes)) throw new Error("Niepoprawny format historii wydań.");
  return notes.filter(release => {
    if (!/^v\d+\.\d+\.\d+$/.test(release.version) || !Array.isArray(release.changes) ||
        !release.changes.every(item => typeof item?.title === "string" && typeof item?.detail === "string")) {
      throw new Error("Niepoprawny wpis historii wydań.");
    }
    const version = release.version.slice(1).split(".").map(Number);
    const difference = version.findIndex((part,i) => part !== installed[i]);
    return difference < 0 || version[difference] < installed[difference];
  });
}

async function clientExe(folder) {
  if (typeof folder !== "string" || !/^[a-z]:[\\/]/i.test(folder)) return null;
  for (const name of clientNames) {
    const exe = path.join(folder, name);
    try { if ((await fs.stat(exe)).isFile()) return exe; } catch {}
  }
  return null;
}

async function clients() {
  const ps = path.join(process.env.SystemRoot, "System32","WindowsPowerShell","v1.0","powershell.exe");
  const query = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); ConvertTo-Json -Compress -InputObject @(Get-Process -Name dbvStart,dbvStart_gl -ErrorAction SilentlyContinue | Select-Object Id,Path,@{Name='HasWindow';Expression={$_.MainWindowHandle -ne [IntPtr]::Zero}})";
  return JSON.parse((await exec(ps, ["-NoProfile","-NonInteractive","-Command",query],{...options,timeout:12000})).stdout);
}

function windowPid(list, folder) {
  const matches = list.filter(proc => proc.HasWindow && proc.Path &&
    path.dirname(proc.Path).toLowerCase() === path.resolve(folder).toLowerCase());
  if (matches.length > 1) {
    const error = new Error("Otwarte są co najmniej dwa okna klienta z tego folderu. Pozostaw jedno.");
    error.code = "MULTIPLE_CLIENTS";
    throw error;
  }
  return matches[0]?.Id || null;
}

async function openWindowPid(folder) {
  return windowPid(await clients(), folder);
}

async function openClient(folder) {
  const exe = await clientExe(folder);
  if (!exe) throw new Error("Wybierz katalog z dbvStart.exe lub dbvStart_gl.exe.");
  const found = await clients();
  const same = found.find(proc => proc.Path && path.dirname(proc.Path).toLowerCase() === path.resolve(folder).toLowerCase());
  if (same) return same.Id;
  if (found.length) throw new Error("Klient działa z innej lokalizacji. Wybierz jego folder.");
  const child = spawn(exe, [], {cwd:folder,detached:true,stdio:"ignore",windowsHide:false});
  await new Promise((ok, fail) => { child.once("spawn",ok); child.once("error",fail); });
  child.unref();
  return child.pid;
}

function interactive(root, id, revision, onLine) {
  const proc = spawn(path.join(root,"venv","Scripts","python.exe"),["-B","-u","-X","utf8","-m","launcher.desktop_session"],
    {cwd:root,windowsHide:true,stdio:["pipe","pipe","pipe"],env:{...process.env,PYTHONIOENCODING:"utf-8",DBV_LAUNCHER_ID:id,DBV_LAUNCHER_REVISION:revision}});
  for (const stream of [proc.stdout,proc.stderr]) {
    stream.setEncoding("utf8");
    readline.createInterface({input:stream}).on("line",onLine);
  }
  proc.stdin.on("error",()=>{}); // The child may exit just before a user submits input.
  return proc;
}

module.exports = {checkRelease,history,validSha,clientExe,clients,windowPid,openWindowPid,openClient,interactive};
