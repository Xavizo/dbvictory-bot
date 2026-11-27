// Loaded before every panel test (npm test): lets the tests' own module loaders
// require the panel's TypeScript modules by their "@/" names, as Next.js resolves them.
const Module = require("node:module");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

const root = path.resolve(__dirname, "..");
const resolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  if (request.startsWith("@/")) {
    const file = path.join(root, request.slice(2));
    for (const ext of ["", ".ts", ".tsx", ".json"]) if (fs.existsSync(file + ext) && fs.statSync(file + ext).isFile()) return file + ext;
  }
  return resolve.call(this, request, ...rest);
};
for (const ext of [".ts", ".tsx"]) require.extensions[ext] = (module, filename) => {
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, "utf8"), { fileName: filename, compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } });
  module._compile(outputText, filename);
};
// Some tests run a compiled module in new Function(), which sees only globals.
globalThis.require = require;
