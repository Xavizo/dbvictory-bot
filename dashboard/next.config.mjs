// The panel is served locally by dbv/mcp_server/panel_server.py; the defaults fit.
// DBV_MOCK=1 (mock/build.mjs) exports static pages for the phone preview instead, with
// relative asset paths so they load from any folder.
/** @type {import("next").NextConfig} */
export default process.env.DBV_MOCK === "1"
  ? { output: "export", assetPrefix: ".", distDir: ".next-mock", images: { unoptimized: true } }
  : {};
