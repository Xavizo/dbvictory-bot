// A small made-up hunting ground for the phone preview (see mock/README.md).
// Everything here is invented: no data comes from the real game.
(() => {
  "use strict";
  const SCOPE = "mock-world";
  const BOUNDS = { left: 960, top: 960, right: 1152, bottom: 1088 }; // three by two 64-tile sectors
  const FLOORS = [7, 8];

  function hash(x, y, z) {
    let h = (x * 374761393 + y * 668265263 + z * 2147483647) | 0;
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  }

  // "clear" | "blocked" | "avoid" | "no_ground", with a terrain colour for the overview map.
  function tile(x, y, z) {
    if (x < BOUNDS.left || x >= BOUNDS.right || y < BOUNDS.top || y >= BOUNDS.bottom) return { kind: "no_ground", color: null };
    if (z === 8) {
      const inCave = (x - 1060) ** 2 / 900 + (y - 1030) ** 2 / 400 < 1 || (Math.abs(y - 1030) < 3 && x > 1020 && x < 1110);
      if (!inCave) return { kind: "no_ground", color: null };
      return hash(x, y, z) < 0.07 ? { kind: "blocked", color: "#5a4a3a" } : { kind: "clear", color: "#8a6d4c" };
    }
    if (x === BOUNDS.left || y === BOUNDS.top || x === BOUNDS.right - 1 || y === BOUNDS.bottom - 1) return { kind: "blocked", color: "#2f3a2a" };
    if ((x - 1100) ** 2 + (y - 1000) ** 2 < 170) return { kind: "blocked", color: "#2c67a8" }; // lake
    if ((x - 1100) ** 2 + (y - 1000) ** 2 < 230) return { kind: "clear", color: "#c9b77a" }; // shore
    if (x >= 990 && x <= 1004 && y >= 1050 && y <= 1060) { // a house
      const wall = x === 990 || x === 1004 || y === 1050 || y === 1060;
      if (wall && !(y === 1060 && x === 997)) return { kind: "blocked", color: "#8a8f99" };
      return { kind: "clear", color: "#a07b52" };
    }
    if (Math.abs(x - 1060) <= 1 || Math.abs(y - 1030) <= 1) return { kind: "clear", color: "#b39b6a" }; // roads
    if (hash(x, y, z) < 0.09) return { kind: "blocked", color: "#1f4d27" }; // trees
    if (hash(x + 7, y, z) < 0.02) return { kind: "avoid", color: "#6d8f3b" }; // bushes
    return { kind: "clear", color: hash(x, y + 3, z) < 0.5 ? "#4f8a3e" : "#4a833a" };
  }

  const STAIRS = [[1061, 1031, 7, 8]]; // [x, y, from floor, to floor]
  function navigation(x, y, z) {
    for (const [sx, sy, from, to] of STAIRS) {
      if (sx === x && sy === y && z === from) return [{ kind: "stairs", item_id: 1385, stack: 0, source: "confirmed_id", direction_hint: to > from ? "down" : "up" }];
      if (sx === x && sy === y && z === to) return [{ kind: "ladder", item_id: 1948, stack: 0, source: "confirmed_id", direction_hint: "up" }];
    }
    return undefined;
  }

  function cell(x, y, z, occupants) {
    const t = tile(x, y, z);
    const nav = navigation(x, y, z);
    const out = { x, y, z, kind: t.kind, stairs: nav ? "confirmed_id" : null, occupants: occupants?.count ?? 0,
      items: t.kind === "no_ground" ? [] : [{ id: t.kind === "blocked" ? 2700 : 4526, flags: t.kind === "blocked" ? ["blocking"] : [], stack: 0, minimap_color: 24 }] };
    if (occupants) out.occupant_types = occupants.types;
    if (nav) out.navigation = nav;
    return out;
  }

  // A loop of waypoints around the hunting ground, and the tile path the character walks.
  const ROUTE = [[1060, 1030, 7], [1080, 1012, 7], [1120, 1020, 7], [1128, 1050, 7], [1090, 1070, 7], [1040, 1072, 7], [1012, 1040, 7], [1030, 1010, 7]];
  function walk(points) {
    const path = [];
    for (let i = 0; i < points.length; i++) {
      const [ax, ay, z] = points[i], [bx, by] = points[(i + 1) % points.length];
      let x = ax, y = ay;
      path.push([x, y, z]);
      while (x !== bx || y !== by) {
        x += Math.sign(bx - x); y += Math.sign(by - y);
        path.push([x, y, z]);
      }
    }
    return path;
  }
  const PATH = walk(ROUTE);

  // An overview picture (base64 PNG) of one 64 x 64 sector, as /api/map/world sends it.
  const sectorCache = new Map();
  function sectorPicture(sx, sy, z) {
    const key = sx + "," + sy + "," + z;
    if (sectorCache.has(key)) return sectorCache.get(key);
    let data = null;
    try {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 64;
      const g = canvas.getContext("2d");
      let any = false;
      for (let y = 0; y < 64; y++) for (let x = 0; x < 64; x++) {
        const t = tile(sx + x, sy + y, z);
        if (!t.color) continue;
        any = true; g.fillStyle = t.color; g.fillRect(x, y, 1, 1);
      }
      data = any ? canvas.toDataURL("image/png").split(",")[1] : null;
    } catch { data = null; }
    sectorCache.set(key, data);
    return data;
  }
  function sectors(z) {
    const rows = [];
    for (let y = BOUNDS.top; y < BOUNDS.bottom; y += 64) for (let x = BOUNDS.left; x < BOUNDS.right; x += 64) {
      const terrain = sectorPicture(x, y, z);
      if (terrain) rows.push({ x, y, terrain });
    }
    return rows;
  }

  window.__dbvMockWorld = Object.freeze({ SCOPE, BOUNDS, FLOORS, ROUTE, PATH, tile, cell, hash, sectors });
})();
