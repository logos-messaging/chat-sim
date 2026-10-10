'use strict';

/* Runs the page's script under node against a stand-in for a document: as much of one as the
   script asks for. Standard input holds the script, the fragment of the page's address, what
   each address is answered, and the steps a reader takes. Standard output holds what stands in
   the document after the first read and after each step. */

const vm = require('node:vm');

/* As much is laid out as says where a reader is. A part of the report is PART high and as much
   higher as its boxes that scroll are, and LINE higher while its line of when the conversation
   was last heard from holds words. The parts stand one under another, less how far the window
   is scrolled, and all that is in a part stands where the part does. A row that keeps a name
   is ROW high, a box that scrolls is as high as its rows and no higher than SIGHT, and a row
   stands ROW under the one before, less how far the box is scrolled, with all that is in it. */
const PART = 100;
const LINE = 5;
const ROW = 10;
const SIGHT = 20;
let scrollY = 0;

class Text {
  constructor(text) {
    this.textContent = String(text);
    this.parentNode = null;
  }
}

/* A selector as the script writes one: a list of a tag, classes and attributes, with no
   combinator. Any other is refused, so that the script cannot ask for what is not answered. */
function matcher(selector) {
  const lists = selector.split(',').map((one) => {
    const parts = [...one.trim().matchAll(/([a-z][a-z0-9]*)|\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g)];
    if (parts.map((part) => part[0]).join('') !== one.trim()) throw new Error(`no such selector here: ${selector}`);
    return parts;
  });
  const holds = (node, [, tag, cls, name, value]) => {
    if (tag) return node.tagName.toLowerCase() === tag;
    if (cls) return node.className.split(' ').includes(cls);
    return value === undefined ? node.hasAttribute(name) : node.getAttribute(name) === value;
  };
  return (node) => lists.some((parts) => parts.every((part) => holds(node, part)));
}

class Element {
  constructor(tag, namespace) {
    this.tagName = namespace ? tag : tag.toUpperCase();
    this.namespaceURI = namespace || 'html';
    this.attributes = new Map();
    this.childNodes = [];
    this.parentNode = null;
    this.listeners = [];
    this.hidden = false;
    this.scrolled = 0;
    Object.assign(this, { scrollLeft: 0, scrollWidth: 0, offsetTop: 0 });
  }

  get named() { return this.querySelectorAll('[data-key]'); }
  get clientHeight() { return this.matches('.scroll') ? Math.min(this.scrollHeight, SIGHT) : 0; }
  get scrollHeight() { return this.named.length * ROW; }
  get scrollTop() { return this.scrolled; }
  /* A box scrolls no further than its rows reach. */
  set scrollTop(top) { this.scrolled = Math.max(0, Math.min(top, this.scrollHeight - this.clientHeight)); }

  get children() { return this.childNodes.filter((node) => node instanceof Element); }
  get parentElement() { return this.parentNode; }
  get isConnected() { return document.body.contains(this); }
  get className() { return this.attributes.get('class') || ''; }
  set className(value) { this.setAttribute('class', value); }
  get textContent() { return this.childNodes.map((node) => node.textContent).join(''); }
  set textContent(value) { this.replaceChildren(...(String(value) ? [String(value)] : [])); }

  setAttribute(name, value) { this.attributes.set(String(name), String(value)); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  hasAttribute(name) { return this.attributes.has(name); }
  removeAttribute(name) { this.attributes.delete(name); }

  append(...nodes) {
    for (const given of nodes) {
      const node = given instanceof Element || given instanceof Text ? given : new Text(given);
      if (node.parentNode) node.parentNode.childNodes = node.parentNode.childNodes.filter((other) => other !== node);
      node.parentNode = this;
      this.childNodes.push(node);
    }
  }

  replaceChildren(...nodes) {
    for (const node of this.childNodes) node.parentNode = null;
    this.childNodes = [];
    this.append(...nodes);
  }

  replaceWith(node) {
    const parent = this.parentNode;
    const place = parent.childNodes.indexOf(this);
    parent.append(node);
    parent.childNodes.splice(place, 1, parent.childNodes.pop());
    this.parentNode = null;
  }

  contains(node) {
    for (let each = node; each; each = each.parentNode) if (each === this) return true;
    return false;
  }

  matches(selector) { return matcher(selector)(this); }

  closest(selector) {
    const holds = matcher(selector);
    for (let each = this; each; each = each.parentNode) if (holds(each)) return each;
    return null;
  }

  querySelectorAll(selector) {
    const holds = matcher(selector);
    const found = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (holds(child)) found.push(child);
        walk(child);
      }
    };
    walk(this);
    return found;
  }

  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }

  focus() { if (this.isConnected && this.matches('button, a, [tabindex]')) document.activeElement = this; }
  blur() { if (document.activeElement === this) document.activeElement = document.body; }

  getBoundingClientRect() {
    const parts = report.children;
    const above = parts.slice(0, Math.max(0, parts.findIndex((part) => part.contains(this))));
    const boxes = above.flatMap((part) => part.querySelectorAll('.scroll'));
    const lines = above.flatMap((part) => part.querySelectorAll('[data-heard]')).filter((line) => line.textContent);
    const stands = above.length * PART + boxes.reduce((sum, box) => sum + box.clientHeight, 0) + lines.length * LINE - scrollY;
    if (this.matches('.scroll')) return { top: stands, bottom: stands + this.clientHeight, left: 0, right: 0 };
    const row = this.closest('[data-key]');
    const box = row && row.closest('.scroll');
    if (!box) return { top: stands, bottom: stands, left: 0, right: 0 };
    const top = stands + box.named.indexOf(row) * ROW - box.scrollTop;
    return { top, bottom: top + ROW, left: 0, right: 0 };
  }

  scrollTo(left, top) { Object.assign(this, { scrollLeft: left, scrollTop: top }); }
  addEventListener(type, listener) { this.listeners.push([type, listener]); }
}

const heard = { document: [], window: [] };
const document = {
  body: new Element('body'),
  visibilityState: 'visible',
  createElement: (tag) => new Element(tag),
  createElementNS: (namespace, tag) => new Element(tag, namespace),
  getElementById: (id) => document.body.querySelector(`[id="${id}"]`),
  addEventListener: (type, listener) => heard.document.push([type, listener]),
};
document.activeElement = document.body;
for (const [tag, id] of [['svg', 'marks'], ['nav', 'rail'], ['div', 'notice'], ['div', 'report'], ['section', 'log']]) {
  const made = new Element(tag, tag === 'svg' ? 'svg' : null);
  made.setAttribute('id', id);
  document.body.append(made);
}
const [, , notice, report, log] = document.body.children;
notice.hidden = true;
log.hidden = true;

/* An event at an element, heard there, at each element around it, at the document and at the
   window, as one that bubbles is. */
function fire(target, type, key) {
  const event = { target, type, key, altKey: false, ctrlKey: false, metaKey: false, preventDefault() {} };
  const around = [];
  for (let each = target; each; each = each.parentNode) around.push(...each.listeners);
  for (const [name, listener] of [...around, ...heard.document, ...heard.window]) {
    if (name === type) listener(event);
  }
}

const given = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const asked = [];
/* The start of the paths whose answers wait, and the answers that wait for it to be let go. */
let held = null;
const waiting = [];

async function fetch(path) {
  asked.push(path);
  if (held !== null && path.startsWith(held)) await new Promise((come) => waiting.push(come));
  const answer = given.answers[path] || { status: 404, text: '{"error": "nothing is answered here"}' };
  const pieces = [new TextEncoder().encode(answer.text)];
  const read = async () => (pieces.length ? { done: false, value: pieces.pop() } : { done: true });
  return { status: answer.status, ok: answer.status === 200, body: { getReader: () => ({ read }) } };
}

/* Every promise of the page is kept before the next turn of the loop: it waits for no timer. */
const settled = () => new Promise((done) => setImmediate(done));

function dump(node) {
  const said = {
    tag: node.tagName.toLowerCase(),
    attrs: Object.fromEntries(node.attributes),
    kids: node.childNodes.map((child) => (child instanceof Text ? child.textContent : dump(child))),
  };
  if (node === document.activeElement) said.focused = true;
  return said;
}

/* The rows in sight in a box, each with how far down the box it starts. */
function sight(box) {
  const { top } = box.getBoundingClientRect();
  return box.named
    .map((row) => [row.getAttribute('data-key'), row.getBoundingClientRect().top - top])
    .filter(([, under]) => under >= 0 && under < box.clientHeight);
}

const state = () => ({
  report: dump(report),
  log: log.hidden ? null : dump(log),
  notice: notice.hidden ? null : notice.textContent,
  asked: asked.splice(0),
  window: scrollY,
  sight: Object.fromEntries(report.querySelectorAll('.scroll').map((box) => [box.className, sight(box)])),
});

function take(step) {
  if (step.answers) {
    Object.assign(given.answers, step.answers);
    for (const [type, listener] of heard.document) if (type === 'visibilitychange') listener({ type });
  } else if (step.hold) {
    held = step.hold;
  } else if (step.come) {
    held = null;
    for (const come of waiting.splice(0)) come();
  } else if (step.hash) {
    location.hash = step.hash;
    for (const [type, listener] of heard.window) if (type === 'hashchange') listener({ type });
  } else if (step.focus) {
    document.body.querySelectorAll(step.focus)[step.nth || 0].focus();
  } else if (step.reach) {
    scrollY += report.querySelector(step.reach).getBoundingClientRect().top;
  } else if (step.scroll) {
    report.querySelector(step.scroll).scrollTop = step.top;
  } else if (step.key) {
    fire(document.activeElement, 'keydown', step.key);
  } else {
    const pressed = document.body.querySelectorAll(step.press)[step.nth || 0];
    /* A press puts the focus on what takes it, and then is a click. */
    if (pressed.matches('button, [tabindex]')) pressed.focus();
    fire(pressed, 'click');
  }
}

async function main() {
  const window = {
    innerHeight: PART,
    scrollBy(left, top) { scrollY += top; },
    scrollTo(left, top) { scrollY = top; },
    addEventListener: (type, listener) => heard.window.push([type, listener]),
  };
  Object.assign(globalThis, { document, window, fetch, location: { hash: given.hash, host: 'collector.test' }, setInterval: () => 0 });
  vm.runInThisContext(given.script, { filename: 'report.js' });
  await settled();
  const states = [state()];
  for (const step of given.steps) {
    /* A list of steps is taken with no answer between them. */
    for (const each of [].concat(step)) take(each);
    await settled();
    states.push(state());
  }
  process.stdout.write(JSON.stringify(states));
}

main();
