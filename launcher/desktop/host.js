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

async function history(root, revision) {
  if (!validSha(revision)) return [];
  const git = path.join(process.env.ProgramFiles || "C:\\Program Files","Git","cmd","git.exe");
  try {
    const {stdout} = await exec(git, ["-c","safe.directory="+root.replaceAll("\\","/"),"-C",root,"log","-5","--format=%s",revision],options);
    return stdout.trim().split(/\r?\n/).filter(Boolean);
  } catch { return []; }
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
  const query = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); ConvertTo-Json -Compress -InputObject @(Get-Process -Name dbvStart,dbvStart_gl -ErrorAction SilentlyContinue | Select-Object Id,Path)";
  return JSON.parse((await exec(ps, ["-NoProfile","-NonInteractive","-Command",query],{...options,timeout:12000})).stdout);
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

module.exports = {checkRelease,history,validSha,clientExe,clients,openClient,interactive};
