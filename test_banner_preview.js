const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const listeners = {};
const makeImageElement = () => ({
  src: "",
  hidden: false,
  classes: new Set(),
  classList: {
    toggle(name, enabled) { if (enabled) this.owner.classes.add(name); else this.owner.classes.delete(name); },
    add(name) { this.owner.classes.add(name); },
    remove(...names) { names.forEach((name) => this.owner.classes.delete(name)); },
  },
  removeAttribute(name) { if (name === "src") this.src = ""; },
  addEventListener() {},
});
const bindClasses = (element) => {
  element.classList.owner = element;
  return element;
};
const boxes = Array.from({ length: 2 }, () => {
  const backdrop = bindClasses(makeImageElement());
  const main = bindClasses(makeImageElement());
  const classes = new Set();
  return {
    classes,
    classList: {
      toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); },
    },
    querySelector(selector) {
      if (selector === "[data-preview-backdrop]") return backdrop;
      if (selector === "[data-preview-main]") return main;
      return null;
    },
    backdrop,
    main,
  };
});
const previews = { hidden: true, querySelectorAll: () => boxes };
const warning = { textContent: "" };
const smartNote = { hidden: true, textContent: "" };
const focusControl = { hidden: false };
const input = {
  files: [],
  addEventListener(event, callback) { listeners[`input:${event}`] = callback; },
};
const radio = (value, checked = false) => ({
  value, checked,
  addEventListener(event, callback) { listeners[`${value}:${event}`] = callback; },
});
const modeInputs = [radio("fill", true), radio("fit")];
const focusInputs = [radio("top"), radio("center", true), radio("bottom")];
const documentStub = {
  querySelector(selector) {
    return {
      '#image[name="image"]': input,
      "[data-banner-previews]": previews,
      "#image-warning": warning,
      "[data-focus-control]": focusControl,
      "[data-smart-fit-note]": smartNote,
    }[selector] || null;
  },
  querySelectorAll(selector) {
    if (selector === "[data-fit-mode]") return modeInputs;
    if (selector === "[data-focus]") return focusInputs;
    return [];
  },
};
const revoked = [];
const URLStub = {
  createObjectURL(file) { assert.equal(file.name, "portrait.jpg"); return "blob:banner-preview-test"; },
  revokeObjectURL(url) { revoked.push(url); },
};
class ImageStub {
  width = 800;
  height = 1000;
  set src(value) { this._src = value; this.onload(); }
  get src() { return this._src; }
}
const windowStub = { addEventListener(event, callback) { listeners[`window:${event}`] = callback; } };
vm.runInNewContext(fs.readFileSync("static/banner-admin.js", "utf8"), {
  document: documentStub,
  window: windowStub,
  URL: URLStub,
  Image: ImageStub,
  console,
  FileReader: class {},
});

input.files = [{ name: "portrait.jpg", type: "image/jpeg" }];
listeners["input:change"]();
for (const box of boxes) {
  assert.equal(box.main.src, "blob:banner-preview-test");
  assert.equal(box.backdrop.src, "blob:banner-preview-test");
  assert.ok(box.classes.has("banner-fit"));
}
assert.equal(previews.hidden, false);
assert.equal(modeInputs[1].checked, true);
assert.equal(smartNote.hidden, false);
assert.match(warning.textContent, /800 x 1000/);
listeners["window:pagehide"]();
assert.deepEqual(revoked, ["blob:banner-preview-test"]);
console.log("ALL BANNER PREVIEW CHECKS PASSED");
