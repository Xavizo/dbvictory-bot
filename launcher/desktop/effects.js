"use strict";
const $ = selector => document.querySelector(selector);
const sparks = [
  [12, 68, 5.6, -2.5], [20, 59, 11.2, -3.1], [29, 49, 5.6, -1.5], [18, 74, 11.2, -.8],
  [82, 56, 11.2, -3.3], [91, 53, 5.6, -2], [95, 40, 11.2, -6], [13, 44, 5.6, -4.3],
];
for (const [x, y, duration, offset] of sparks) {
  const dot = document.createElement("i");
  dot.className = "cinder";
  dot.style.setProperty("--x", x + "%");
  dot.style.setProperty("--y", y + "%");
  dot.style.setProperty("--duration", duration + "s");
  dot.style.setProperty("--offset", offset + "s");
  $(".energy").append(dot);
}
const layouts = [
  [[50, 52]], [[34, 53], [65, 53]], [[50, 33], [34, 65], [65, 65]],
  [[34, 37], [66, 37], [34, 67], [66, 67]],
  [[32, 34], [68, 34], [50, 52], [32, 71], [68, 71]],
];
