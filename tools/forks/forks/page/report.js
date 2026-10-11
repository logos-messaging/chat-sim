'use strict';

/* The fork report's page. Every verdict, position, count, order and grouping it shows is the
   collector's: the script asks, builds elements and listens. What a log said reaches the
   document as text and never as markup. */

(() => {
  const POLL = 30000;
  /* How long the next piece of an answer is waited for before the collector is said not to answer. */
  const PATIENCE = 20000;

  /* The lineage: a column an epoch, a row a line of states, in pixels of the text at its usual
     size. The drawing is REM of them to a rem, so it grows with the text. */
  const REM = 16;
  const COLUMN = 72;
  const ROW = 64;
  const NODE = 13;
  const LABELS = 128;
  const AXIS = 28;
  /* The timeline: the steps its stamps may be apart. */
  const DAY = 86400;
  const AXIS_STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 21600, 43200, DAY];
  /* The room the words under a split's first epoch take, and those at a branch's newest state. */
  const CAPTION = 200;
  const ENDS = 136;
  /* A tile names its installations while they are no more than this many. */
  const NAMED = 3;

  const HUES = ['b0', 'b1', 'b2'];
  const SPLIT = {
    mark: 'split',
    tone: '',
    gloss: 'Installations hold different states at one epoch. No branch is the group: each is what the installations on it take the group to be.',
  };
  /* The mark and the tone each other verdict is drawn with, and what it says of the group. */
  const VERDICTS = new Map([
    ['whole', { mark: 'whole', tone: 'ok', gloss: 'Every live installation is at the tip of one line of states.' }],
    ['whole, members behind', { mark: 'behind', tone: 'warn', gloss: 'No epoch holds two states, and a live installation is behind.' }],
    ['whole as far as seen', { mark: 'unknown', tone: 'warn', gloss: 'No epoch holds two states, and a later state stands where no link from a live installation leads: the logs prove neither a split nor its absence.' }],
    ['not enough data', { mark: 'nodata', tone: 'quiet', gloss: 'No state was reported by two installations. One log alone cannot show a split.' }],
    ['no live member', { mark: 'ended', tone: 'quiet', gloss: 'No installation is at the tip, behind or of unknown lineage.' }],
  ]);
  const UNNAMED = { mark: 'nodata', tone: 'quiet', gloss: '' };
  const POSITIONS = new Map([
    ['tip', { mark: 'whole', gloss: 'nothing newer is known on its line of states' }],
    ['behind', { mark: 'behind', gloss: 'links lead from its newest state to a later one' }],
    ['lineage unknown', { mark: 'unknown', gloss: 'no link says whether a later state follows its own' }],
    ['ended', { mark: 'ended', gloss: 'every run it wrote in is final' }],
    ['removed', { mark: 'removed', gloss: 'it left, or no newest roster lists it' }],
    ['never joined', { mark: 'never', gloss: 'its log is here and holds none of the group' }],
    ['no data', { mark: 'nodata', gloss: 'nobody shared its log' }],
  ]);
  const HEADINGS = new Map([
    ['one line', 'The one line'],
    ['neither branch', 'On neither branch'],
    ['not placed', 'Not placed'],
  ]);
  /* How a lane's cell is drawn at an epoch its installation wrote nothing at. */
  const EMPTY = new Map([
    ['unreached', { cls: 'never', word: 'not reached' }],
    ['listed', { cls: 'hatch', word: 'no log shared' }],
  ]);
  /* How what became of a post on an installation is drawn: in a cell of the messages, and on
     the timeline as a circle of a radius or as its sign. */
  const FATES = new Map([
    ['sent', { cls: 'sent', text: 'sent', r: 4.5 }],
    ['read', { cls: 'read', sign: '\u2713', text: 'read', r: 3.5 }],
    ['unreadable', { cls: 'unread', sign: '\u2715', text: 'unreadable' }],
    ['no record', { cls: 'none', text: 'no record' }],
    ['no log', { cls: 'hatch' }],
    ['not a member', { cls: 'out' }],
  ]);

  const sprite = document.getElementById('marks');
  const rail = document.getElementById('rail');
  const notice = document.getElementById('notice');
  const report = document.getElementById('report');
  const log = document.getElementById('log');

  /* What each part of the page was last drawn from: an answer that says the same draws nothing. */
  const drawn = { rail: {}, report: {} };
  /* What the list last said of when a conversation was heard from: its id, the words, the stamp. */
  let lastHeard = {};
  /* The record behind each mark that opens log lines, by the number the mark carries. */
  let marks = [];
  let runs = new Map();
  /* The one mark of a section that the Tab key stops at; the arrow keys move it. */
  const stops = new WeakMap();
  let answered = null;
  let turn = 0;
  /* The read that is under way, so that none begun by the clock takes its place. */
  let reading = 0;
  let opened = 0;
  let opener = null;
  /* The excerpt that is open: the record it shows, and where its bar says what the record is. */
  let excerpt = null;
  /* The conversation whose every post the reader asked for. */
  let whole = null;
  /* The conversation that is drawn, as the collector told it. */
  let told = null;
  /* What a reader opened, by the names its buttons keep from one drawing to the next: a fold's
     lanes, a post's installations under a fold, the installations behind a count. */
  const unfolded = new Set();
  /* The parts of a folded conversation that grow with its group, as they stand drawn. */
  let parts = new Map();
  /* What the collector said last at each address it gave a validator for. */
  const tagged = new Map();
  /* What the arrow keys move between: a mark that opens log lines, and a button that unfolds. */
  const STOPS = '[data-m], [data-unfolds]';

  function el(tag, cls, text) {
    const made = document.createElement(tag);
    if (cls) made.className = cls;
    if (text !== undefined) made.textContent = text;
    return made;
  }

  function into(parent, ...children) {
    parent.append(...children);
    return parent;
  }

  function svg(tag, attributes, text) {
    const made = document.createElementNS(sprite.namespaceURI, tag);
    for (const [name, value] of Object.entries(attributes || {})) made.setAttribute(name, value);
    if (text !== undefined) made.textContent = text;
    return made;
  }

  function markOf(name) {
    const drawing = svg('svg', { class: 'm', viewBox: '0 0 24 24', 'aria-hidden': 'true', focusable: 'false' });
    return into(el('span', `st ${name}`), into(drawing, svg('use', { href: `#mark-${name}` })));
  }

  function link(cls, id, text) {
    const made = el('a', cls, text);
    made.setAttribute('href', `#${encodeURIComponent(id)}`);
    return made;
  }

  /* Data as one part of a path. Dots alone would name the directory above, so they name nothing. */
  const part = (id) => (/^\.*$/.test(id) ? null : encodeURIComponent(id));

  function shown() {
    const fragment = location.hash.slice(1);
    try {
      return fragment ? decodeURIComponent(fragment) : null;
    } catch (malformed) {
      return fragment;
    }
  }

  /* What became of a call: what the collector said, or why there is nothing to read. */
  async function ask(path) {
    if (path === null) return { gone: true };
    const stop = new AbortController();
    let timer;
    /* An answer may take as long as its bytes do: the wait is for the next of them. */
    const wait = () => {
      clearTimeout(timer);
      timer = setTimeout(() => stop.abort(), PATIENCE);
    };
    try {
      wait();
      const had = tagged.get(path);
      const answer = await fetch(path, { signal: stop.signal, cache: 'no-store', headers: had ? { 'If-None-Match': had.tag } : {} });
      /* The collector says what it said last: it sends nothing, and nothing is read again. */
      if (answer.status === 304 && had) return had;
      const pieces = answer.body.getReader();
      const decoder = new TextDecoder();
      let text = '';
      for (let piece = await pieces.read(); !piece.done; piece = await pieces.read()) {
        wait();
        text += decoder.decode(piece.value, { stream: true });
      }
      text += decoder.decode();
      if (answer.status === 401) return { refused: true };
      if (answer.status === 404) return { gone: true };
      if (!answer.ok) return { silent: true };
      return { path, tag: answer.headers.get('ETag'), text, said: JSON.parse(text) };
    } catch (unanswered) {
      return unanswered instanceof SyntaxError ? { unreadable: true } : { silent: true };
    } finally {
      clearTimeout(timer);
    }
  }

  function say(text) {
    if (notice.textContent !== text) notice.textContent = text;
    notice.hidden = !text;
  }

  /* A moment as the collector stamps one, in UTC. */
  const utc = (moment) => `${moment.toISOString().slice(0, 19)}Z`;

  function sayWhy(answer) {
    const unread = answer.refused || answer.silent || answer.unreadable;
    if (unread && drawn.report.text === undefined) report.replaceChildren();
    const then = answered ? ` What is drawn is what the collector said at ${utc(answered)}.` : '';
    if (answer.refused) {
      say('The collector did not take this browser\'s token. Open the share address again to read on.');
    } else if (answer.unreadable) {
      say(`The answer at this address is not one this page can read.${then}`);
    } else if (answer.silent && answered) {
      say(`The collector does not answer. It last answered at ${utc(answered)}, and what is drawn is what it said then.`);
    } else if (answer.silent) {
      say('The collector does not answer, and has not since this page was opened.');
    }
    return Boolean(unread);
  }

  async function read() {
    const mine = ++turn;
    reading = mine;
    const id = shown();
    const [listing, convo] = await Promise.all([
      ask('conversations'),
      id === null ? null : ask(part(id) && `conversations/${part(id)}`),
    ]);
    if (reading === mine) reading = 0;
    if (mine !== turn) return;
    /* Something that has no list of conversations is not the collector. */
    if (sayWhy(listing.gone ? { silent: true } : listing) || (convo && sayWhy(convo))) return;
    /* What a reader opened is of the conversation that is drawn, until another is drawn in its place. */
    const asked = [whole, [...unfolded]];
    if (drawn.report.view !== id) {
      whole = null;
      unfolded.clear();
    }
    try {
      redraw(rail, 'rail', id, listing.text, () => railOf(listing.said, id));
      if (id === null) {
        redraw(report, 'report', id, listing.text, () => frontPage(listing.said));
      } else if (convo.gone) {
        redraw(report, 'report', id, '', () => goneOf(id));
      } else {
        redraw(report, 'report', id, convo.text, () => conversation(convo.said));
        heardOf(listing.said, id);
      }
    } catch (shaped) {
      /* JSON of another shape than the collector's: what is drawn stays as it was. */
      if (drawn.report.view !== id) {
        [whole] = asked;
        for (const key of asked[1]) unfolded.add(key);
      }
      sayWhy({ unreadable: true });
      return;
    }
    /* What is drawn is held under its validator: the list, and the conversation shown alone. */
    tagged.clear();
    for (const said of [listing, convo]) if (said && said.tag) tagged.set(said.path, said);
    answered = new Date();
    say('');
  }

  /* Draws a part of the page anew. A reader of the same view is left where they were in it. A
     control whose press asks for the drawing has it made whatever the answer, and stays where
     it is. */
  function redraw(container, name, view, text, build, pressed = null) {
    const was = drawn[name];
    if (!pressed && was.view === view && was.text === text) return;
    const started = performance.now();
    const stays = was.view === view;
    const held = stays && container.contains(document.activeElement) ? document.activeElement : null;
    const record = held && recordOf(held);
    const place = stays ? placeIn(container, pressed) : null;
    const scrolled = [...container.querySelectorAll('.scroll')].map((box) => [box.scrollLeft, box.scrollTop, inSight(box)]);
    /* Each table keeps the stop it had, where that is drawn still. */
    const stood = stays
      ? [...container.querySelectorAll('[data-roving]')].map((group) => stops.get(group)).filter(Boolean).map((stop) => [stop, recordOf(stop)])
      : [];
    const made = build();
    /* A focused element that leaves the document leaves the Tab key going on from where it stood. */
    if (container.contains(document.activeElement)) document.activeElement.blur();
    /* A part that stands as it was drawn stays where it is, and is not laid out again. */
    if (made.length === container.children.length && made.some((each) => each.parentNode === container)) [...container.children].forEach((child, index) => child === made[index] || child.replaceWith(made[index]));
    else container.replaceChildren(...made);
    drawn[name] = { view, text };
    container.querySelectorAll('.scroll').forEach((box, index) => {
      if (stays && scrolled[index]) settle(box, ...scrolled[index]);
      /* The newest epochs are what a lane is opened for. */
      else if (box.hasAttribute('data-newest')) box.scrollTo(box.scrollWidth, 0);
    });
    for (const group of container.querySelectorAll('[data-roving]')) {
      const first = group.querySelector(STOPS);
      if (first) hold(first);
    }
    if (place) {
      const same = place.record
        ? markOfRecord(container, place.record)
        : [...container.querySelectorAll('[data-key]')].find((made) => made.getAttribute('data-key') === place.key);
      if (same) window.scrollBy(0, same.getBoundingClientRect().top - place.top);
    }
    /* The mark of the open lines first: what was a stop and what had the focus go back over it. */
    if (excerpt && container === report) under(container);
    for (const [stop, wrote] of stood) again(container, stop, wrote, false);
    if (held) again(container, held, record);
    /* A new view begins at its title, for the keyboard and for a screen reader. */
    if (!stays && was.view !== undefined && container === report) report.querySelector('h1').focus({ preventScroll: true });
    performance.measure(`draw ${name}`, { start: started, end: performance.now() });
  }

  /* The rows in sight in a box that scrolls, by the names they keep from one drawing to the
     next, each with how far down the box it starts. */
  function inSight(box) {
    const { top, bottom } = box.getBoundingClientRect();
    return [...box.querySelectorAll('[data-key]')]
      .map((row) => ({ key: row.getAttribute('data-key'), top: row.getBoundingClientRect().top - top }))
      .filter((row) => row.top >= 0 && row.top < bottom - top);
  }

  /* Scrolls a box that was drawn anew to where it stood. The first row that was in sight and
     is drawn still starts as far down the box as it did, whatever came or went above it, as
     far as the box scrolls. */
  function settle(box, left, top, sight) {
    box.scrollTo(left, top);
    const rows = new Map([...box.querySelectorAll('[data-key]')].map((row) => [row.getAttribute('data-key'), row]));
    const kept = sight.find((row) => rows.has(row.key));
    if (kept) box.scrollTop += rows.get(kept.key).getBoundingClientRect().top - box.getBoundingClientRect().top - kept.top;
  }

  /* Whether a box that scrolls down holds this: what is in one is the box's to keep in place. */
  function boxed(made) {
    const box = made.closest('.scroll');
    return Boolean(box) && box.scrollHeight > box.clientHeight;
  }

  /* The part at the middle of the window, by the name it keeps from one drawing to the next,
     and how far down the window it starts. */
  function placeIn(container, pressed) {
    /* Whoever presses a control is at the part it stands in. */
    const part = pressed && pressed.closest('[data-key]');
    if (part) return { key: part.getAttribute('data-key'), top: part.getBoundingClientRect().top };
    /* Whoever reads log lines is at the mark that opened them, while it is in the window. */
    if (excerpt && opener && container.contains(opener) && !boxed(opener)) {
      const { top, bottom } = opener.getBoundingClientRect();
      if (bottom > 0 && top < window.innerHeight) return { record: excerpt.record, top };
    }
    let found = null;
    for (const made of container.querySelectorAll('[data-key]')) {
      if (boxed(made)) continue;
      const { top } = made.getBoundingClientRect();
      if (top <= window.innerHeight / 2) found = { key: made.getAttribute('data-key'), top };
    }
    return found;
  }

  const recordOf = (mark) => (mark.hasAttribute('data-m') ? marks[Number(mark.getAttribute('data-m'))] : null);

  /* The mark that opens, in what is drawn now, the record a mark of an earlier drawing opened. */
  function markOfRecord(container, record) {
    const number = marks.findIndex((mark) => mark.section === record.section && mark.run === record.run && mark.line === record.line);
    return number < 0 ? null : container.querySelector(`[data-m="${number}"]`);
  }

  /* Puts the focus back on what stands where an element stood, or makes it the stop of its
     table alone. */
  function again(container, held, record, focus = true) {
    const place = held.getAttribute('href');
    const name = held.getAttribute('aria-label');
    const key = held.getAttribute('data-unfolds');
    /* The lanes and the timeline each have a button for a fold: the one in the same row is it. */
    const row = (made) => made.closest('[data-key]').getAttribute('data-key');
    let same;
    if (record) same = markOfRecord(container, record);
    else if (place !== null) same = [...container.querySelectorAll('a')].find((made) => made.getAttribute('href') === place);
    else if (key !== null) same = [...container.querySelectorAll('[data-unfolds]')].find((made) => made.getAttribute('data-unfolds') === key && row(made) === row(held));
    else same = [...container.querySelectorAll('[tabindex], button')].find((made) => made.tagName === held.tagName && made.getAttribute('aria-label') === name && !made.hasAttribute('data-unfolds'));
    if (same && (record || (key !== null && same.closest('[data-roving]')))) hold(same);
    if (same && focus) same.focus({ preventScroll: true });
  }

  /* An open excerpt goes on under a new drawing in that drawing's words, and closes when its
     record is drawn no more. */
  function under(container) {
    const mark = markOfRecord(container, excerpt.record);
    if (!mark) {
      closeLines();
      return;
    }
    hold(mark);
    opener = mark;
    excerpt.record = recordOf(mark);
    excerpt.word.textContent = excerpt.record.said;
  }

  function hold(mark) {
    const group = mark.closest('[data-roving]');
    const was = stops.get(group);
    /* A button is a stop of the Tab key unless it says otherwise. */
    if (was && was !== mark && was.hasAttribute('data-unfolds')) was.setAttribute('tabindex', '-1');
    else if (was && was !== mark) was.removeAttribute('tabindex');
    mark.setAttribute('tabindex', '0');
    stops.set(group, mark);
  }

  /* Seconds as the largest unit that fits them. */
  function ago(seconds) {
    for (const [size, unit] of [[86400, 'd'], [3600, 'h'], [60, 'min']]) {
      if (seconds >= size) return `${Math.floor(seconds / size)} ${unit} ago`;
    }
    return `${seconds} s ago`;
  }

  /* A verdict of a split names its epoch, so it is known by how it begins. */
  const verdictOf = (entry) => (/^split (at|seen from) epoch /.test(entry.verdict) ? SPLIT : VERDICTS.get(entry.verdict) || UNNAMED);
  const sharing = (coverage) => `${coverage.sharing} of ${coverage.seats}`;

  function meter(coverage) {
    const bar = svg('svg', { viewBox: `0 0 ${coverage.seats || 1} 1`, preserveAspectRatio: 'none', 'aria-hidden': 'true', focusable: 'false' });
    return into(el('span', 'meter'), into(bar, svg('rect', { width: coverage.sharing, height: 1 })));
  }

  function linesOf(lines) {
    const said = [el('p', 'quiet', `Lines: ${lines.lines} read, ${lines.records} records, ${lines.unstamped} without a stamp, ${lines.malformed} malformed, ${lines.unknown} unknown.`)];
    if (lines.malformed) said.unshift(el('p', 'notice', skipped(lines.malformed)));
    return said;
  }

  const skipped = (malformed) => (malformed === 1
    ? '1 malformed line was skipped: every verdict here was drawn without it.'
    : `${malformed} malformed lines were skipped: every verdict here was drawn without them.`);

  /* ---- the rail and the front page ---- */

  function railOf(listing, id) {
    const brand = into(el('a', 'brand'), el('b', '', 'Fork report'), el('span', '', location.host));
    brand.setAttribute('href', '#');
    const list = el('ul', 'convs');
    for (const entry of listing.conversations) {
      const { mark } = verdictOf(entry);
      const made = into(
        link(entry.id === id ? 'conv on' : 'conv', entry.id),
        markOf(mark),
        el('span', 'id', entry.id),
        el('span', 'age', ago(entry.unheard)),
        el('span', 'say', entry.verdict),
        el('span', 'cov', `${sharing(entry.coverage)} seats share a log`),
      );
      if (entry.id === id) made.setAttribute('aria-current', 'page');
      list.append(into(el('li'), made));
    }
    return [
      brand,
      el('div', 'railhead', 'Conversations'),
      list,
      el('p', 'railfoot', 'A run the collector has not heard from for thirty days is deleted, and its log with it.'),
    ];
  }

  function frontPage(listing) {
    const head = into(
      el('div', 'head'),
      into(el('div'), title('', 'Conversations'), el('p', 'sub', 'Every conversation a log the collector holds names, the worst first.')),
    );
    if (!listing.conversations.length) {
      return [head, el('p', 'quiet', 'No log the collector holds names a conversation.'), ...linesOf(listing.lines)];
    }
    const titles = into(el('tr'), ...['State', 'Conversation', 'Seats that share a log', 'Branches', 'Last heard from'].map((title) => {
      const made = el('th', '', title);
      made.setAttribute('scope', 'col');
      return made;
    }));
    const body = el('tbody');
    for (const entry of listing.conversations) {
      const { mark } = verdictOf(entry);
      const heard = el('td', '', ago(entry.unheard));
      heard.setAttribute('title', entry.heard);
      const named = link('mono', entry.id, entry.id);
      named.setAttribute('title', entry.id);
      const row = into(
        el('tr'),
        into(el('td'), into(el('span', 'status'), markOf(mark), el('span', 'word', entry.verdict))),
        into(el('td'), named),
        into(el('td'), into(meter(entry.coverage), el('span', '', sharing(entry.coverage)))),
        el('td', '', entry.branches.map((branch) => `${branch.label} at epoch ${branch.epoch}`).join(', ')),
        heard,
      );
      row.setAttribute('data-key', `conversation ${entry.id}`);
      body.append(row);
    }
    const table = into(el('table', 'ov'), into(el('thead'), titles), body);
    return [head, into(el('div', 'card scroll'), table), ...linesOf(listing.lines)];
  }

  /* A view's title, which takes the focus when the view is opened. */
  function title(cls, text) {
    const made = el('h1', cls, text);
    made.setAttribute('tabindex', '-1');
    return made;
  }

  /* A conversation's id as a title: one line of it, and the whole of it on hover. */
  function titleOf(id) {
    const made = title('mono', id);
    made.setAttribute('title', id);
    return made;
  }

  function goneOf(id) {
    const back = el('a', '', 'All conversations');
    back.setAttribute('href', '#');
    return [
      into(el('div', 'head'), into(el('div'), titleOf(id))),
      el('p', 'notice', 'The collector holds no log that names this conversation.'),
      into(el('p'), back),
    ];
  }

  /* When the conversation shown was last heard from, which the list says and its report does not. */
  function heardOf(listing, id) {
    const made = report.querySelector('[data-heard]');
    const entry = listing.conversations.find((each) => each.id === id);
    lastHeard = { id, said: entry ? `Last heard from ${ago(entry.unheard)}` : '', stamp: entry ? entry.heard : '' };
    if (made.textContent !== lastHeard.said) made.textContent = lastHeard.said;
    made.setAttribute('title', lastHeard.stamp);
  }

  /* ---- a conversation ---- */

  function conversation(convo, pressed = false) {
    const split = convo.split_epoch !== null;
    const { posts, fold } = postsOf(convo, pressed);
    const at = {
      split,
      hues: new Map(convo.branches.map((branch, index) => [branch.label, HUES[index] || 'bn'])),
      states: new Map(convo.states.map((state) => [`${state.epoch}:${state.state}`, state])),
      seats: new Map(convo.installations.map((seat) => [seat.id, seat])),
      columns: new Map(convo.epochs.map((epoch, index) => [epoch, index])),
      order: convo.lanes.flatMap((group) => group.installations),
      runs: new Map(convo.runs.map((run) => [run.id, run])),
      posts,
      fold,
      marks: [],
      /* The rows the installations fold into, where the collector says the page starts so. */
      folds: convo.folded ? convo.folds : null,
      unfolds: new Set(),
      parts: new Map(),
    };
    /* The posts a part draws, where it draws any. */
    const ofPosts = `${posts.length} ${fold}`;
    const back = el('a', 'sub', 'All conversations');
    back.setAttribute('href', '#');
    /* The line is drawn as the list last filled it: empty, it would take no room until it is
       filled again, and what stands under it would be measured a line too high. */
    const known = lastHeard.id === convo.id;
    const heard = el('p', 'sub', known ? lastHeard.said : '');
    heard.setAttribute('data-heard', '');
    heard.setAttribute('title', known ? lastHeard.stamp : '');
    const made = [
      into(el('div', 'head'), into(el('div'), back, titleOf(convo.id), heard)),
      verdict(convo),
      tiles(convo, at),
      section('Lineage of states', 'a circle is a state and holds how many installations reported it, a line is a link a log proves', lineage(convo, at)),
      asDrawn('Installations', '', convo, at, () => section('Installations', at.folds ? 'a row is the installations of one position, a column an epoch, a cell how many of them reported a state there; a row opens to a row for each' : 'a row is an installation, a column an epoch, a cell the state it reported there', ...lanes(convo, at))),
      asDrawn('Timeline', ofPosts, convo, at, () => section('Timeline', at.folds ? 'a line is the installations of one position: a band from the first to the last of them reaching an epoch, with the letter of its branch; times are UTC' : 'a line is a run, a mark where its installation reached an epoch with the letter of its branch, dots where its delivery was not online; times are UTC', ...timeline(convo, at))),
      asDrawn('Messages', ofPosts, convo, at, () => section('Messages', at.folds ? 'a row is a post, a column the installations of one position, a cell how many of them each thing became of the post on' : 'a row is a post, a column an installation, a cell what became of the post there', ...messages(convo, at))),
      section(split ? 'The state each branch holds' : 'The state the group holds', 'the newest state of each branch, and whom its roster lists', holds(convo, at)),
      ...linesOf(convo.lines),
    ];
    /* While the page reads on, the record whose log lines are open stays drawn: where no row
       that is open holds it now, the fold of its installation is opened for it, among the
       lanes or under its post, and the conversation is drawn with that. */
    const sought = at.folds && excerpt && convo !== told ? excerpt.record : null;
    const lost = sought && !at.marks.some((mark) => mark.section === sought.section && mark.run === sought.run && mark.line === sought.line) ? holderOf(convo, sought) : null;
    if (lost && !unfolded.has(lost)) {
      unfolded.add(lost);
      return conversation(convo, pressed);
    }
    for (const { made: stands, by } of at.parts.values()) {
      if (by) for (const mark of stands.querySelectorAll('[data-m]')) mark.setAttribute('data-m', Number(mark.getAttribute('data-m')) + by);
    }
    /* Kept once the whole is drawn: a drawing that fails leaves the marks of the one before. */
    parts = at.parts;
    marks = at.marks;
    runs = at.runs;
    told = convo;
    /* What is drawn no more is open no more. */
    for (const key of [...unfolded]) if (!at.unfolds.has(key)) unfolded.delete(key);
    return made;
  }

  /* A part of a conversation that grows with its group. On a folded page a press draws anew
     only the parts it changes: one stands while each of its buttons says what is open and it
     draws the same posts, and its marks are numbered on from those now before it. */
  function asDrawn(name, shows, convo, at, build) {
    const from = at.marks.length;
    const was = convo === told ? parts.get(name) : undefined;
    const buttons = was ? [...was.made.querySelectorAll('[data-unfolds]')] : [];
    const key = (button) => button.getAttribute('data-unfolds');
    if (was && was.shows === shows && buttons.every((button) => button.getAttribute('aria-expanded') === String(unfolded.has(key(button))))) {
      for (const mark of was.marks) at.marks.push(mark);
      for (const button of buttons) at.unfolds.add(key(button));
      at.parts.set(name, { made: was.made, shows, from, marks: was.marks, by: from - was.from });
      return was.made;
    }
    const made = build();
    if (at.folds) at.parts.set(name, { made, shows, from, marks: at.marks.slice(from) });
    return made;
  }

  /* What a reader opens on a folded page to have a record drawn: the fold of the installation
     that wrote it, and for a record a count of the messages stands for, that count. */
  function holderOf(convo, record) {
    const wrote = (at) => at.run === record.run && at.line === record.line;
    const message = convo.messages.find((each) => Object.values(each.marks).some(wrote));
    const seat = message
      ? Object.keys(message.marks).find((id) => wrote(message.marks[id]))
      : (convo.installations.find((each) => each.history.some((step) => wrote(step.at))) || {}).id;
    const fold = convo.folds.find((each) => each.installations.includes(seat));
    if (!fold) return null;
    return record.section === 'messages' ? countName(message, fold) : foldName(fold);
  }

  function section(name, gloss, ...inside) {
    const made = el('section', 'sec');
    made.setAttribute('aria-label', name);
    made.setAttribute('data-key', name);
    return into(made, into(el('div', 'sechead'), el('h2', '', name), el('p', '', gloss)), ...inside);
  }

  function verdict(convo) {
    const { mark, tone, gloss } = verdictOf(convo);
    const made = el('section', tone ? `verdict ${tone}` : 'verdict');
    made.setAttribute('aria-label', 'Verdict');
    made.setAttribute('data-key', 'Verdict');
    const cover = into(el('span', 'cover'), meter(convo.coverage), el('span', '', `${sharing(convo.coverage)} seats share a log`));
    const said = into(el('div'), into(el('div', 'vhead'), el('h2', '', convo.verdict), cover));
    if (gloss) said.append(el('p', '', gloss));
    said.append(el('p', '', 'The count is of the seats that are neither ended nor removed. A split among seats that share no log does not show.'));
    if (convo.lines.malformed) said.append(el('p', 'skipped', skipped(convo.lines.malformed)));
    return into(made, markOf(mark), said);
  }

  function tiles(convo, at) {
    const made = el('dl', 'tiles');
    made.setAttribute('data-key', 'Tiles');
    made.append(tile('Seats that share a log', sharing(convo.coverage), 'of those neither ended nor removed'));
    if (at.split) {
      const labels = convo.branches.map((branch) => branch.label);
      made.append(tile('Branches', labels.length, `the first epoch with two states is ${convo.split_epoch}`, labels));
    }
    const asked = [];
    for (const { position, count, installations } of convo.positions) {
      /* Folded, a count of more than a tile names says who they are when it is asked. */
      const of = `that are ${position}`;
      const asks = at.folds && count > NAMED ? names(count, of, at) : null;
      made.append(tile(position, count, (POSITIONS.get(position) || { gloss: '' }).gloss, count > NAMED ? [] : installations, asks));
      if (asks && unfolded.has(of)) asked.push(into(el('p', 'named'), el('b', '', position), ` ${installations.join(', ')}`));
    }
    /* Names that were asked for stand under the tiles, on lines as wide as their row: no tile
       moves, and the button that asked for them stays where it is. */
    return asked.length ? into(el('div', 'tiled'), made, ...asked) : made;
  }

  function tile(name, value, gloss, who = [], asks = null) {
    const made = into(el('div', 'tile'), el('dt', '', name), el('dd', 'v', value), el('dd', 'd', gloss));
    if (asks) made.append(into(el('dd', 'asks'), asks));
    if (who.length) made.append(el('dd', 'who', who.join(', ')));
    return made;
  }

  /* The heading a group of installations is read under. */
  const headed = (group) => (group.under === 'branch' ? `Branch ${group.branch}` : HEADINGS.get(group.under) || group.under);
  /* The name a fold keeps from one drawing to the next: its heading and its position. */
  const foldName = (fold) => `${headed(fold)}, ${fold.position}`;
  /* The name a post's count under a fold keeps. */
  const countName = (message, fold) => `${message.frame} under ${foldName(fold)}`;
  const counted = (count) => (count === 1 ? '1 installation' : `${count} installations`);

  /* A button that opens what stands under a name and closes it again, and says which of the
     two a press will do. Among the cells the arrow keys move between it is one of them, and a
     stop of the Tab key only while it is the one held. */
  function unfolds(cls, key, name, at, roving = false) {
    const made = el('button', cls);
    made.setAttribute('type', 'button');
    made.setAttribute('aria-expanded', unfolded.has(key));
    made.setAttribute('aria-label', name);
    made.setAttribute('data-unfolds', key);
    if (roving) made.setAttribute('tabindex', '-1');
    at.unfolds.add(key);
    return made;
  }

  /* A button that names the installations a count stands for: those `of` something. */
  const names = (count, of, at) => into(unfolds('btn', of, `Names of all ${count} ${of}`, at), `Names of all ${count}`);

  /* What is drawn under a heading, in order: an installation each, by its id. Folded, a fold
     for each position, and after one a reader opened its installations. */
  function rowsOf(group, at) {
    if (!at.folds) return group.installations;
    const under = at.folds.filter((fold) => fold.under === group.under && fold.branch === group.branch);
    return under.flatMap((fold) => (unfolded.has(foldName(fold)) ? [fold, ...fold.installations] : [fold]));
  }

  /* How a state is drawn wherever it stands: its class, its letter and its word. */
  function look(state, at) {
    const hue = (label) => at.hues.get(label) || 'bn';
    if (state && state.lineage === 'branch') return { cls: hue(state.branch), letter: state.branch, word: `branch ${state.branch}` };
    if (state && state.lineage === 'side') return { cls: `side ${hue(state.toward)}`, letter: state.toward, word: `only the logs of branch ${state.toward} reach it` };
    if (state && state.lineage === 'shared') return { cls: 'shared', letter: '\u00b7', word: 'shared line' };
    return { cls: 'unknown', letter: '?', word: 'lineage unknown' };
  }

  function lineage(convo, at) {
    if (!convo.states.length) return el('p', 'quiet', 'No log holds a state of this conversation.');
    /* Each row is a line of states, where the collector puts it, and is named for its newest
       state: the states come by epoch, so that is the last of the row's. */
    const newest = new Map(convo.states.map((state) => [state.row, state]));
    const height = AXIS + ROW * newest.size;
    const x = (epoch) => LABELS + at.columns.get(epoch) * COLUMN + COLUMN / 2;
    const y = (row) => AXIS + ROW * row + ROW / 2;
    const placed = new Map(convo.states.map((state) => [`${state.epoch}:${state.state}`, { x: x(state.epoch), y: y(state.row) }]));
    const cut = at.split ? x(convo.split_epoch) - COLUMN / 2 : 0;
    const width = Math.max(LABELS + convo.epochs.length * COLUMN + (at.split ? ENDS : NODE * 3), at.split ? cut + CAPTION : 0);
    const drawing = svg('svg', { width: `${width / REM}rem`, height: `${(height + 16) / REM}rem`, viewBox: `0 0 ${width} ${height + 16}`, role: 'img', 'aria-label': 'The states and the links between them. The installations below list each state by epoch.' });
    for (const epoch of convo.epochs) drawing.append(svg('text', { class: 'ep', x: x(epoch), y: 12, 'text-anchor': 'middle' }, epoch));
    drawing.append(svg('text', { class: 'ep', x: 8, y: 12 }, 'EPOCH'));
    const named = (state) => {
      if (state.lineage === 'branch') return `Branch ${state.branch}`;
      if (state.lineage === 'side') return `Only ${state.toward}'s logs`;
      return state.lineage === 'shared' ? 'Shared line' : 'Lineage unknown';
    };
    for (const [row, state] of [...newest].sort(([one], [other]) => one - other)) drawing.append(svg('text', { x: 8, y: y(row) + 4 }, named(state)));
    if (at.split) {
      drawing.append(svg('line', { class: 'cut', x1: cut, x2: cut, y1: AXIS - 8, y2: height }));
      drawing.append(svg('text', { class: 'ep', x: cut + 6, y: height + 12 }, 'the first epoch with two states'));
    }
    const nodes = [];
    for (const state of convo.states) {
      const here = placed.get(`${state.epoch}:${state.state}`);
      const { cls, word } = look(state, at);
      for (const parent of state.after) {
        const from = placed.get(`${parent.epoch}:${parent.state}`);
        const bend = (from.x + here.x) / 2;
        drawing.append(svg('path', { class: `link ${cls}`, d: `M${from.x + NODE} ${from.y}C${bend} ${from.y} ${bend} ${here.y} ${here.x - NODE} ${here.y}` }));
      }
      nodes.push(into(
        svg('g'),
        svg('title', {}, `epoch ${state.epoch}, state ${state.state}, ${word}, reported by ${state.holders}, ${state.members} in its roster`),
        svg('circle', { class: cls, cx: here.x, cy: here.y, r: NODE }),
        svg('text', { class: 'n', x: here.x, y: here.y + 4, 'text-anchor': 'middle' }, state.holders),
        svg('text', { class: 'fp', x: here.x, y: here.y - NODE - 6, 'text-anchor': 'middle' }, state.state.slice(0, 8)),
      ));
    }
    drawing.append(...nodes);
    if (at.split) {
      for (const branch of convo.branches) {
        const tip = placed.get(`${branch.epoch}:${branch.state}`);
        drawing.append(svg('text', { class: 'end', x: tip.x + NODE + 6, y: tip.y - 1 }, `Branch ${branch.label}`));
        drawing.append(svg('text', { class: 'ep', x: tip.x + NODE + 6, y: tip.y + 12 }, `epoch ${branch.epoch}, ${branch.installations.length} on it`));
      }
    }
    return scroller('lineage', 'The lineage of states', drawing);
  }

  /* A card that scrolls what is wider or longer than the page, from the keyboard too. */
  function scroller(cls, name, inside) {
    const made = el('div', `card scroll ${cls}`);
    made.setAttribute('tabindex', '0');
    made.setAttribute('role', 'group');
    made.setAttribute('aria-label', name);
    return into(made, inside);
  }

  function legend(convo, at) {
    const entry = (swatch, text) => into(el('li'), swatch, el('span', '', text));
    const made = el('ul', 'legend');
    made.append(entry(el('span', 'sw shared'), 'shared line'));
    if (at.split) {
      for (const branch of convo.branches) made.append(entry(el('span', `sw ${at.hues.get(branch.label)}`), `branch ${branch.label}`));
      made.append(entry(el('span', 'sw side'), 'only one branch\'s logs reach it'));
    }
    made.append(
      entry(el('span', 'sw unknown'), 'lineage unknown'),
      entry(el('span', 'sw never'), 'not reached'),
      entry(el('span', 'sw hatch'), 'no log shared'),
      entry(markOf('removed'), 'removed'),
      entry(markOf('ended'), 'ended'),
    );
    return made;
  }

  /* A mark that opens the log lines around a record. A record has one mark in a section. */
  function opens(made, step, said, section, at) {
    made.setAttribute('role', 'button');
    made.setAttribute('aria-label', said);
    made.setAttribute('data-m', at.marks.push({ section, run: step.at.run, line: step.at.line, said }) - 1);
    return made;
  }

  function lanes(convo, at) {
    const tail = convo.epochs.length + 1;
    const titles = into(el('tr'), heading('who', 'Installation'), heading('', 'Account'), heading('', 'Name'), heading('', 'Sent by'));
    for (const epoch of convo.epochs) titles.append(heading('e', epoch));
    titles.append(heading('e', ''), heading('where', 'Where it is'), heading('heard', 'Last heard from'));
    const body = el('tbody');
    for (const group of convo.lanes) {
      const title = group.under === 'branch' ? `Branch ${group.branch}` : HEADINGS.get(group.under) || group.under;
      const under = el('th');
      under.setAttribute('colspan', tail + 6);
      under.setAttribute('scope', 'rowgroup');
      const heads = into(el('tr', 'grp'), into(under, into(el('span'), el('b', '', title), el('span', '', group.positions.join(', ')))));
      heads.setAttribute('data-key', `under ${title}`);
      body.append(heads);
      for (const each of rowsOf(group, at)) body.append(typeof each === 'string' ? lane(at.seats.get(each), convo, at, tail) : foldLane(each, convo, at, tail));
    }
    const table = into(el('table', 'lanes'), into(el('thead'), titles), body);
    table.setAttribute('data-roving', '');
    const box = scroller('lanesbox', 'The installations, epoch by epoch', table);
    box.setAttribute('data-newest', '');
    return [
      legend(convo, at),
      el('p', 'quiet', at.folds
        ? 'A row counts, at each epoch, the installations of one position that hold a record there, each count in the colour of the state they hold, and opens to a row for each of them. A cell that holds a record opens the log lines around it. The arrow keys move between the rows and such cells, and Enter opens one.'
        : 'A cell that holds a record opens the log lines around it. The arrow keys move between such cells, and Enter opens one.'),
      box,
    ];
  }

  function heading(cls, title) {
    const made = el('th', cls, title);
    made.setAttribute('scope', 'col');
    return made;
  }

  function lane(seat, convo, at, tail) {
    const who = el('th', 'who mono', seat.id);
    who.setAttribute('scope', 'row');
    /* A run of the installation a line, in the order it wrote in them. */
    const ofRuns = (cls, said) => into(el('td', cls), ...seat.runs.map((id) => el('span', 'run', said(at.runs.get(id)))));
    const row = into(el('tr'), who, el('td', 'mono dim', seat.account || ''), ofRuns('', (run) => run.name), ofRuns('dim', (run) => run.app));
    row.setAttribute('data-row', '');
    row.setAttribute('data-key', `lane ${seat.id}`);
    const last = seat.cells[seat.cells.length - 1];
    /* A lane that holds no cell is its words, from where a roster first lists the seat. */
    const drawnTo = last ? at.columns.get(last.epoch) : (seat.listed.length ? at.columns.get(seat.listed[0]) : 0) - 1;
    const cells = new Map(seat.cells.map((held) => [held.epoch, held]));
    for (const epoch of convo.epochs.slice(0, drawnTo + 1)) row.append(into(el('td', 'e'), ...cell(cells.get(epoch), seat, at)));
    const over = el('td', 'e over');
    over.setAttribute('colspan', tail - drawnTo - 1);
    const { mark } = POSITIONS.get(seat.position) || { mark: 'nodata' };
    if (['removed', 'ended', 'never joined'].includes(seat.position)) {
      /* A leave the lane ends in is the mark of its removal. */
      const leaves = last && last.steps.length && seat.history[last.steps[last.steps.length - 1]].kind === 'left';
      if (!leaves) over.append(into(el('span', 'endmark'), markOf(mark)));
      over.append(el('span', '', seat.kept ? `${seat.position}, its log is gone` : seat.position));
    }
    const where = [seat.position];
    if (seat.branch !== null && at.split) where.push(`branch ${seat.branch}`);
    if (seat.epoch !== null) where.push(`epoch ${seat.epoch}`);
    else if (seat.listed.length) where.push(`listed from epoch ${seat.listed[0]}`);
    if (seat.behind_by !== null) where.push(`${seat.behind_by} behind`);
    const heard = seat.runs.length
      ? ofRuns('heard mono dim', (run) => `${run.heard}${run.final ? ', final' : ''}`)
      : el('td', 'heard mono dim', seat.kept ? 'log gone' : 'no log');
    return into(row, over, into(el('td', 'where'), into(el('span', 'pos'), markOf(mark), el('span', '', where.join(', ')))), heard);
  }

  /* A fold among the lanes: how many of its installations hold a record at each epoch, where
     they are, and from when to when the collector last heard from their runs. */
  function foldLane(fold, convo, at, tail) {
    const name = foldName(fold);
    const count = fold.installations.length;
    const who = into(el('th', 'who'), into(unfolds('tog', name, `${name}: ${counted(count)}`, at, true), fold.position), el('span', 'dim', counted(count)));
    who.setAttribute('scope', 'row');
    /* A fold has no account, name or sender of its own. */
    const none = el('td');
    none.setAttribute('colspan', 3);
    const row = into(el('tr', 'fold'), who, none);
    row.setAttribute('data-row', '');
    row.setAttribute('data-key', `fold ${name}`);
    const last = fold.cells[fold.cells.length - 1];
    /* A fold that holds no cell is its words, from where a roster first lists one of its seats. */
    const drawnTo = last ? at.columns.get(last.epoch) : (fold.listed_from ? at.columns.get(fold.listed_from.low) : 0) - 1;
    const cells = new Map(fold.cells.map((held) => [held.epoch, held]));
    for (const epoch of convo.epochs.slice(0, drawnTo + 1)) row.append(into(el('td', 'e'), ...stack(cells.get(epoch), count, at)));
    const over = el('td', 'e over');
    over.setAttribute('colspan', tail - drawnTo - 1);
    const { mark } = POSITIONS.get(fold.position) || { mark: 'nodata' };
    if (['removed', 'ended', 'never joined'].includes(fold.position)) {
      over.append(into(el('span', 'endmark'), markOf(mark)));
      const gone = count === 1 ? 'its log is gone' : `the log is gone of ${fold.kept}`;
      over.append(el('span', '', fold.kept ? `${fold.position}, ${gone}` : fold.position));
    }
    const reach = ({ low, high }) => (low === high ? `${low}` : `${low} to ${high}`);
    const epochs = (range) => `${range.low === range.high ? 'epoch' : 'epochs'} ${reach(range)}`;
    const where = [fold.position];
    if (fold.epoch) where.push(epochs(fold.epoch));
    if (fold.listed_from) where.push(`listed from ${epochs(fold.listed_from)}`);
    if (fold.behind_by) where.push(`${reach(fold.behind_by)} behind`);
    const stands = into(el('td', 'where'), into(el('span', 'pos'), markOf(mark), el('span', '', where.join(', '))));
    /* An installation is counted under each state it wrote at an epoch, and under a leave: where
       one wrote more than one of them, the counts there are more than the installations. */
    for (const held of fold.cells) {
      if (held.several) stands.append(el('span', 'more', `at epoch ${held.epoch}, ${held.several} of them wrote more than one state, or a state and a leave, and ${held.several === 1 ? 'is' : 'are'} counted under each`));
    }
    const heard = convo.heard[convo.folds.indexOf(fold)];
    /* A line for each thing said, as a lane has a line for each run: the column stays narrow. */
    const said = [];
    if (heard.runs) said.push(...(heard.first === heard.last ? [heard.first] : [`${heard.first} to`, heard.last]));
    if (heard.final) said.push(`${heard.final} of ${heard.runs} ${heard.runs === 1 ? 'run' : 'runs'} final`);
    if (fold.logs < count) said.push(fold.logs ? `no log held of ${count - fold.logs}` : 'no log held');
    return into(row, over, stands, into(el('td', 'heard mono dim'), ...said.map((line) => el('span', 'run', line))));
  }

  /* What a fold holds at an epoch: how many of its installations wrote each state there, how
     many a leave, and how many of its lanes hold what no record says, each as a lane draws it. */
  function stack(held, count, at) {
    if (held === undefined) return [];
    const sum = (cls, word, ...inside) => {
      const made = into(el('span', `cell sum ${cls}`), ...inside);
      const said = `epoch ${held.epoch}, ${inside[inside.length - 1]} of ${counted(count)}, ${word}`;
      made.setAttribute('role', 'img');
      made.setAttribute('aria-label', said);
      made.setAttribute('title', said);
      return made;
    };
    const made = held.states.map((each) => {
      const { cls, letter, word } = look(at.states.get(`${held.epoch}:${each.state}`), at);
      /* Off the shared line a count carries its letter, so that its colour is not all that says it. */
      return sum(cls, `${word}, state ${each.state}`, ...(cls === 'shared' ? [] : [el('b', '', letter)]), each.count);
    });
    if (held.left) made.push(sum('left', 'left', markOf('removed'), held.left));
    for (const [kind, { cls, word }] of EMPTY) {
      if (held[kind]) made.push(sum(cls, word, held[kind]));
    }
    return made;
  }

  /* What a lane holds at an epoch: each record its installation wrote there, in the order written. */
  function cell(held, seat, at) {
    if (held === undefined) return [];
    if (!held.steps.length) {
      const { cls, word } = EMPTY.get(held.kind) || { cls: '', word: held.kind };
      const empty = el('span', `cell ${cls}`);
      empty.setAttribute('role', 'img');
      empty.setAttribute('aria-label', `epoch ${held.epoch}, ${word}`);
      return [empty];
    }
    return held.steps.map((place) => {
      const step = seat.history[place];
      if (step.kind === 'left') {
        return opens(into(el('span', 'cell left'), markOf('removed')), step, `${seat.id}, epoch ${step.epoch}, left`, 'lanes', at);
      }
      const { cls, letter, word } = look(at.states.get(`${step.epoch}:${step.state}`), at);
      return opens(el('span', `cell ${cls}`, letter), step, `${seat.id}, epoch ${step.epoch}, ${word}, state ${step.state}`, 'lanes', at);
    });
  }

  function timeline(convo, at) {
    const { start, end } = convo.timeline;
    /* Where a moment stands across the plot. A run that reaches past the plot's edges is cut
       there. */
    const x = (seconds) => `${(end > start ? ((seconds - start) / (end - start)) * 100 : 0).toFixed(4)}%`;
    const step = AXIS_STEPS.find((size) => (end - start) / size <= 8) || DAY * Math.ceil((end - start) / DAY / 8);
    const stamp = (seconds) => new Date(Math.round(seconds * 1000)).toISOString();
    const ticks = [];
    for (let count = Math.ceil(start / step); count * step <= end; count += 1) ticks.push(count * step);
    /* Down a plot everything stands at a share of its height, which report.css sets by the
       size of the text. */
    const axis = svg('svg', { class: 'plot', role: 'img', 'aria-label': `From ${stamp(start)} to ${stamp(end)}` });
    let date = null;
    for (const tick of ticks) {
      const day = stamp(tick).slice(0, 10);
      const time = stamp(tick).slice(11, step < 1 ? 21 : 19);
      axis.append(svg('text', { class: 'axis', x: x(tick), y: '36%', 'text-anchor': 'middle' }, step < DAY ? time : day));
      /* The date stands under the first stamp of each day. */
      if (step < DAY && day !== date) axis.append(svg('text', { class: 'axis', x: x(tick), y: '79%', 'text-anchor': 'middle' }, day));
      date = day;
    }
    /* The posts each installation wrote a record of, in the order sent. */
    const wrote = new Map(at.order.map((id) => [id, []]));
    for (const message of at.posts) {
      for (const id of Object.keys(message.marks)) wrote.get(id).push(message);
    }
    /* A fold's line: at each epoch a band from the first of its installations reaching a state
       to the last of them, and while it is closed the posts they sent. */
    const strand = (fold) => {
      const name = foldName(fold);
      const count = fold.installations.length;
      const who = into(el('th'), into(unfolds('tog', name, `${name}: ${counted(count)}`, at, true), name), el('span', 'dim', counted(count)));
      who.setAttribute('scope', 'row');
      const row = into(el('tr', 'fold'), who);
      row.setAttribute('data-row', '');
      row.setAttribute('data-key', `span ${name}`);
      /* Of installations without a log that is held, some shared one that is gone and some none. */
      const gone = count === 1 ? 'its log is gone' : 'their logs are gone';
      const none = fold.kept ? `the log is gone of ${fold.kept}, no log shared of ${count - fold.kept}` : 'no log shared';
      if (!fold.logs) return into(row, into(el('td'), into(el('div', 'plotbox hatched'), el('span', 'said', fold.kept === count ? gone : none))));
      const strip = svg('svg', { class: 'plot' });
      for (const tick of ticks) strip.append(svg('line', { class: 'grid', x1: x(tick), x2: x(tick), y1: 0, y2: '100%' }));
      for (const held of fold.cells) {
        for (const each of held.states) {
          const { cls, letter, word } = look(at.states.get(`${held.epoch}:${each.state}`), at);
          const said = `${name}, epoch ${held.epoch}, ${word}, ${each.count} of ${counted(count)}, from ${stamp(each.first)} to ${stamp(each.last)}`;
          const band = into(
            svg('g', { role: 'img', 'aria-label': said }),
            svg('title', {}, said),
            svg('line', { class: `reach ${cls}`, x1: x(each.first), x2: x(each.last), y1: '50%', y2: '50%' }),
            ...[each.first, each.last].map((seconds) => svg('rect', { class: `tick ${cls}`, x: x(seconds), y: '23%', width: 3, height: '54%', rx: 1, transform: 'translate(-1.5)' })),
          );
          if (cls !== 'shared') band.append(svg('text', { class: 'letter', x: x(each.first), y: '15%', 'text-anchor': 'middle', 'aria-hidden': 'true' }, letter));
          strip.append(band);
        }
      }
      /* A record has one mark in a section: open, a post is on the line of its installation. */
      for (const id of unfolded.has(name) ? [] : fold.installations) {
        for (const message of wrote.get(id)) {
          const mark = message.marks[id];
          if (message.cells[id] !== 'sent') continue;
          strip.append(opens(svg('circle', { class: 'post sent', cx: x(mark.seconds), cy: '50%', r: FATES.get('sent').r }), { at: mark }, `${fate(message, id)}, at ${mark.time}`, 'timeline', at));
        }
      }
      return into(row, into(el('td'), into(el('div', 'plotbox'), strip)));
    };
    const body = el('tbody');
    for (const each of at.folds ? convo.lanes.flatMap((group) => rowsOf(group, at)) : at.order) {
      if (typeof each !== 'string') {
        body.append(strand(each));
        continue;
      }
      const id = each;
      const seat = at.seats.get(id);
      const who = el('th', 'mono', id);
      who.setAttribute('scope', 'row');
      const row = into(el('tr'), who);
      row.setAttribute('data-row', '');
      row.setAttribute('data-key', `time ${id}`);
      body.append(row);
      if (!seat.spans.length) {
        row.append(into(el('td'), into(el('div', 'plotbox hatched'), el('span', 'said', seat.kept ? 'its log is gone' : 'no log shared'))));
        continue;
      }
      const strip = svg('svg', { class: 'plot' });
      for (const tick of ticks) strip.append(svg('line', { class: 'grid', x1: x(tick), x2: x(tick), y1: 0, y2: '100%' }));
      for (const span of seat.spans) {
        strip.append(svg('line', { class: span.online ? 'on' : 'off', x1: x(span.start), x2: x(span.end), y1: '50%', y2: '50%' }));
      }
      for (const made of seat.history) {
        const { cls, letter, word } = made.kind === 'left' ? { cls: '', word: 'left' } : look(at.states.get(`${made.epoch}:${made.state}`), at);
        const tick = svg('rect', { class: `tick ${cls}`, x: x(made.at.seconds), y: '23%', width: 3, height: '54%', rx: 1, transform: 'translate(-1.5)' });
        strip.append(opens(tick, made, `${id}, epoch ${made.epoch}, ${word}, at ${made.at.time}`, 'timeline', at));
        /* Off the shared line a mark carries its letter, so that its colour is not all that says it. */
        if (letter && cls !== 'shared') strip.append(svg('text', { class: 'letter', x: x(made.at.seconds), y: '15%', 'text-anchor': 'middle', 'aria-hidden': 'true' }, letter));
      }
      for (const message of wrote.get(id)) {
        const mark = message.marks[id];
        const { cls, sign, r } = FATES.get(message.cells[id]) || { cls: '', r: 3.5 };
        const made = r
          ? svg('circle', { class: `post ${cls}`, cx: x(mark.seconds), cy: '50%', r })
          : svg('text', { class: `post ${cls}`, x: x(mark.seconds), y: '50%', 'text-anchor': 'middle', 'dominant-baseline': 'central' }, sign);
        strip.append(opens(made, { at: mark }, `${fate(message, id)}, at ${mark.time}`, 'timeline', at));
      }
      row.append(into(el('td'), into(el('div', 'plotbox'), strip)));
    }
    const titles = into(el('tr'), heading('', 'Installation'), into(heading('', ''), into(el('div', 'plotbox'), axis)));
    const table = into(el('table', 'times'), into(el('thead'), titles), body);
    table.setAttribute('data-roving', '');
    const box = scroller('timeline', 'The timeline', table);
    const folded = at.folds
      ? [el('p', 'quiet', 'A line that stands for several installations draws when the first and the last of them reached each state, and the posts they sent. It leaves out the rest of what a line for each of them draws, which is the run itself and when its delivery was not online, a leave, and the posts read or unreadable. A line opens to one for each of its installations, and the arrow keys move between the lines and their marks.')]
      : [];
    if (!at.posts.length) return [...folded, box];
    const entry = (shape, text) => {
      const key = svg('svg', { class: 'key', viewBox: '-7 -7 14 14', 'aria-hidden': 'true', focusable: 'false' });
      return into(el('li'), into(key, shape), el('span', '', text));
    };
    const legend = into(
      el('ul', 'legend'),
      entry(svg('circle', { class: 'post sent', r: FATES.get('sent').r }), 'sent a post'),
      entry(svg('circle', { class: 'post read', r: FATES.get('read').r }), 'read one'),
      entry(svg('text', { class: 'post unread', 'text-anchor': 'middle', 'dominant-baseline': 'central' }, FATES.get('unreadable').sign), 'one arrived, unreadable'),
    );
    if (at.fold === 'none' || at.fold === 'all') return [legend, ...folded, box];
    return [legend, ...folded, el('p', 'quiet', `The posts marked are those the messages below draw, of ${convo.messages.length}.`), box];
  }

  /* ---- the messages ---- */

  /* The posts that are drawn, and how they fold: `none` where they are no more than the
     collector says a page starts with, `all` once the reader asked for every one, and until
     then `newest`, as many as it says. While the page reads on, a post whose record the reader
     has open or in focus stays drawn, and those after it: `kept` where that is more than the
     newest. A press of the reader's own keeps none. */
  function postsOf(convo, pressed) {
    const newest = convo.messages.length - convo.shown_messages;
    if (newest <= 0) return { posts: convo.messages, fold: 'none' };
    if (whole === convo.id) return { posts: convo.messages, fold: 'all' };
    let from = newest;
    for (const record of pressed ? [] : [excerpt && excerpt.record, recordOf(document.activeElement)]) {
      const held = record ? convo.messages.findIndex((message) => Object.values(message.marks).some((mark) => mark.run === record.run && mark.line === record.line)) : -1;
      if (held >= 0) from = Math.min(from, held);
    }
    /* Folded, so does a post a count of which the reader opened, and one whose row holds the focus. */
    if (convo.folded && !pressed) {
      const row = report.contains(document.activeElement) && document.activeElement.closest('[data-key]');
      const key = row ? row.getAttribute('data-key') : null;
      const held = convo.messages.findIndex((message) => key === `post ${message.frame}` || convo.folds.some((fold) => unfolded.has(countName(message, fold))));
      if (held >= 0) from = Math.min(from, held);
    }
    return { posts: convo.messages.slice(from), fold: from < newest ? 'kept' : 'newest' };
  }

  /* What became of a post on an installation, in words. */
  function fate(message, id) {
    const why = message.why[id];
    return `${id}, frame ${message.frame} from ${message.sender}, ${message.cells[id]}${why ? `, ${why}` : ''}`;
  }

  /* The state a post was sent in, as the lanes draw it, or its epoch alone where its sender
     reported no state there. */
  function sentIn(message, at) {
    const made = el('span', 'chip');
    if (message.state === null) {
      made.setAttribute('title', `epoch ${message.epoch}, its sender reported no state there`);
      return into(made, `epoch ${message.epoch}`);
    }
    const { cls, letter, word } = look(at.states.get(`${message.epoch}:${message.state}`), at);
    const swatch = el('span', `sw ${cls}`);
    swatch.setAttribute('role', 'img');
    swatch.setAttribute('aria-label', word);
    made.setAttribute('title', `epoch ${message.epoch}, state ${message.state}, ${word}`);
    return into(made, swatch, cls === 'shared' ? `epoch ${message.epoch}` : `${letter} \u00b7 epoch ${message.epoch}`);
  }

  /* A post's cell on an installation. One that stands for a record opens the lines around it. */
  function post(message, id, at) {
    const kind = message.cells[id];
    const { cls, sign, text } = FATES.get(kind) || { cls: '', text: kind };
    const made = el('span', `mk ${cls}`);
    if (sign) made.append(el('b', '', sign));
    if (text) made.append(text);
    /* A cell that holds no word says what it is to a pointer as well. */
    else made.setAttribute('title', kind);
    if (message.why[id]) made.append(el('small', '', message.why[id]));
    const mark = message.marks[id];
    if (mark) return opens(made, { at: mark }, fate(message, id), 'messages', at);
    made.setAttribute('role', 'img');
    made.setAttribute('aria-label', fate(message, id));
    return made;
  }

  /* How many installations a thing became of a post on, drawn as a cell of that thing is. */
  function became(kind, count) {
    const { cls, sign, text } = FATES.get(kind) || { cls: '', text: kind };
    /* A cell that holds no word is its swatch beside the words. */
    const made = text ? el('span', `mk ${cls}`) : into(el('span', 'mk'), el('span', `sw ${cls}`));
    if (sign) made.append(el('b', '', sign));
    return into(made, `${count} ${kind}`);
  }

  /* A post's cell under a fold: how many of the fold's installations each thing became of the
     post on, and under the unreadable how many gave each cause. It opens to the installations. */
  function tally(message, fold, fates, at) {
    const causes = Object.entries(fates.why).map(([cause, count]) => `${count} ${cause}`);
    const said = Object.entries(fates.cells).map(([kind, count]) => (kind === 'unreadable' ? `${count} ${kind} (${causes.join(', ')})` : `${count} ${kind}`));
    const made = unfolds('tally', countName(message, fold), `frame ${message.frame} from ${message.sender}, ${foldName(fold)}: ${said.join(', ')}`, at, true);
    for (const [kind, count] of Object.entries(fates.cells)) {
      const part = became(kind, count);
      if (kind === 'unreadable') part.append(...causes.map((cause) => el('small', '', cause)));
      made.append(part);
    }
    return made;
  }

  /* An installation among those a count of a post stands for. One that wrote a record of the
     post opens the lines around it. */
  function among(message, id, at) {
    const made = el('span', 'mk mono', id);
    if (message.why[id]) made.append(el('small', '', message.why[id]));
    const mark = message.marks[id];
    if (mark) return opens(made, { at: mark }, fate(message, id), 'messages', at);
    made.setAttribute('role', 'img');
    made.setAttribute('aria-label', fate(message, id));
    return made;
  }

  /* The row under a post: for each fold the reader opened its count under, the installations
     by what became of the post on them. None where no count of the post is open. */
  function whom(message, at) {
    const open = [...at.folds.entries()].filter(([, fold]) => unfolded.has(countName(message, fold)));
    if (!open.length) return null;
    const inside = el('td');
    inside.setAttribute('colspan', 4 + at.folds.length);
    for (const [index, fold] of open) {
      const part = into(el('div', 'whom'), el('b', '', `${foldName(fold)}, ${counted(fold.installations.length)}`));
      for (const [kind, count] of Object.entries(message.folds[index].cells)) {
        const list = el('ul', 'roster');
        for (const id of fold.installations) {
          if (message.cells[id] === kind) list.append(into(el('li'), among(message, id, at)));
        }
        part.append(became(kind, count), list);
      }
      inside.append(part);
    }
    const row = into(el('tr', 'whose'), inside);
    row.setAttribute('data-row', '');
    /* Its marks stand line under line: the arrow keys go through them as they are read. */
    row.setAttribute('data-wraps', '');
    row.setAttribute('data-key', `who ${message.frame}`);
    return row;
  }

  function messages(convo, at) {
    const unmatched = convo.unmatched_unreadable
      ? [into(el('p', 'quiet'), el('b', '', `Reports of an unreadable frame of no known post: ${convo.unmatched_unreadable}.`), ' Each is of a frame that could not be read and that no shared log says was a post, and a frame has one for each time an installation reported it. A vote or any other encrypted frame of the group counts as much as a post whose sender shares no log.')]
      : [];
    if (!convo.messages.length) return [el('p', 'quiet', 'No log holds a post of this conversation.'), ...unmatched];
    const titles = into(el('tr'), ...['Sent, UTC', 'Frame', 'From', 'Sent in'].map((title) => {
      const made = heading(title === 'Frame' ? 'frame' : '', title);
      made.setAttribute('rowspan', 2);
      return made;
    }));
    for (const group of convo.lanes) {
      const branch = group.under === 'branch';
      const under = el('th', branch ? `under ${at.hues.get(group.branch)}` : 'under');
      under.setAttribute('colspan', at.folds ? group.positions.length : group.installations.length);
      under.setAttribute('scope', 'col');
      titles.append(into(under, el('span', '', branch ? `Branch ${group.branch}` : HEADINGS.get(group.under) || group.under)));
    }
    const body = el('tbody');
    for (const message of at.posts) {
      const sent = el('td', 'mono', message.utc);
      sent.setAttribute('title', message.at.time);
      const frame = el('th', 'frame mono', message.frame);
      frame.setAttribute('scope', 'row');
      frame.setAttribute('title', message.frame);
      const row = into(el('tr'), sent, frame, el('td', 'mono', message.sender), into(el('td'), sentIn(message, at)));
      row.setAttribute('data-row', '');
      row.setAttribute('data-key', `post ${message.frame}`);
      /* Folded, a cell counts the installations of a fold by what became of the post on them. */
      if (at.folds) row.append(...at.folds.map((fold, index) => into(el('td', 'm'), tally(message, fold, message.folds[index], at))));
      else for (const id of at.order) row.append(into(el('td', 'm'), post(message, id, at)));
      body.append(row);
      const who = at.folds && whom(message, at);
      if (who) body.append(who);
    }
    const columns = at.folds
      ? at.folds.map((fold) => into(heading('m', fold.position), el('span', 'of', counted(fold.installations.length))))
      : at.order.map((id) => heading('m', id));
    const table = into(el('table', 'posts'), into(el('thead'), titles, into(el('tr'), ...columns)), body);
    table.setAttribute('data-roving', '');
    const entry = (swatch, text) => into(el('li'), swatch, el('span', '', text));
    const legend = into(
      el('ul', 'legend'),
      entry(el('span', 'mk none', FATES.get('no record').text), 'its log is shared and holds no record of the frame'),
      entry(el('span', 'sw hatch'), 'no log shared'),
      entry(el('span', 'sw out'), 'not a member when the post was sent'),
    );
    const made = [
      legend,
      el('p', 'quiet', at.folds
        ? 'A cell counts the installations of one position by what became of the post on them, and under unreadable stands how many gave each cause. It opens to those installations under the post, and one of them that holds a record opens the log lines around it. The arrow keys move between the cells, and Enter opens one.'
        : 'A cell that holds a record opens the log lines around it. Under unreadable stands the cause its installation gave. The arrow keys move between such cells, and Enter opens one.'),
    ];
    if (at.fold !== 'none') {
      const newest = `The newest ${convo.shown_messages} of ${convo.messages.length} posts are drawn, here and on the timeline`;
      const said = {
        all: `All ${convo.messages.length} posts are drawn, here and on the timeline.`,
        newest: `${newest}.`,
        kept: `${newest}, and the older ones back to the one that is open or has the focus.`,
      }[at.fold];
      const rest = el('button', 'btn', at.fold === 'all' ? `Draw the newest ${convo.shown_messages} only` : `Draw all ${convo.messages.length}`);
      rest.setAttribute('type', 'button');
      rest.setAttribute('data-rest', '');
      made.push(into(el('p', 'rest'), el('span', '', said), rest));
    }
    return [...made, scroller('postsbox', 'The messages, post by post', table), ...unmatched];
  }

  function holds(convo, at) {
    const made = el('div', 'states');
    for (const branch of convo.branches) {
      const card = el('article', at.split ? `card state ${at.hues.get(branch.label)}` : 'card state');
      const facts = el('dl', 'kv');
      for (const [name, value] of [['Epoch', branch.epoch], ['State', branch.state], ['In its roster', branch.members], ['Its first state', `epoch ${branch.since}`]]) {
        facts.append(el('dt', '', name), el('dd', name === 'State' ? 'mono' : '', value));
      }
      const newest = at.states.get(`${branch.epoch}:${branch.state}`);
      card.setAttribute('data-key', `state ${branch.label}`);
      made.append(into(
        card,
        el('h3', '', `Branch ${branch.label}`),
        facts,
        el('h4', 'subhead', 'On it'),
        chips(branch.installations, at, 'no installation', `on branch ${branch.label}`),
        el('h4', 'subhead', 'Whom its roster lists'),
        chips(newest ? newest.roster : [], at, 'nobody', `in the roster of branch ${branch.label}`),
      ));
    }
    if (!convo.branches.length) made.append(el('p', 'quiet', 'No log holds a state of this conversation.'));
    return made;
  }

  function chips(ids, at, none, of) {
    if (!ids.length) return el('p', 'quiet', none);
    /* Folded, more installations than a tile names are a count, and their names when it is asked. */
    const asks = at.folds && ids.length > NAMED ? into(el('p', 'count'), el('span', '', counted(ids.length)), names(ids.length, of, at)) : null;
    if (asks && !unfolded.has(of)) return asks;
    const made = el('ul', 'roster');
    for (const id of ids) {
      const seat = at.seats.get(id);
      const stands = [seat.position];
      if (seat.branch !== null && at.split) stands.push(`on ${seat.branch}`);
      made.append(into(el('li', 'chip'), el('span', 'mono', id), el('em', '', stands.join(', '))));
    }
    return asks ? into(el('div'), asks, made) : made;
  }

  /* ---- the log lines behind a mark ---- */

  async function showLines(mark) {
    const mine = ++opened;
    const record = recordOf(mark);
    const { run, line } = record;
    const answer = await ask(part(run) && `runs/${part(run)}/log?line=${encodeURIComponent(line)}`);
    if (mine !== opened || sayWhy(answer)) return;
    const close = el('button', 'btn', 'Close');
    close.setAttribute('type', 'button');
    const held = runs.get(run);
    const word = el('span', '', record.said);
    const bar = into(el('div', 'bar'), el('b', '', held ? held.name : run), el('span', '', `line ${line}`), word, el('span', 'spacer'), close);
    const card = into(el('div', 'card logx'), bar);
    if (answer.gone) {
      card.append(el('p', 'quiet', 'The collector no longer holds this run\'s log.'));
    } else {
      const text = el('pre');
      text.setAttribute('tabindex', '0');
      text.setAttribute('aria-label', 'The lines around the record');
      try {
        for (const each of answer.said.lines) {
          const made = into(el('span', each.line === line ? 'hit' : ''), el('span', 'ln', each.line), el('span', '', each.text));
          if (each.line === line) made.setAttribute('aria-current', 'true');
          text.append(made);
        }
      } catch (shaped) {
        sayWhy({ unreadable: true });
        return;
      }
      card.append(text);
    }
    /* The report may have been drawn anew while the lines were asked for, and without their mark. */
    const stands = mark.isConnected ? mark : markOfRecord(report, record);
    if (!stands) return;
    opener = stands;
    hold(opener);
    excerpt = { record, word };
    log.replaceChildren(card);
    log.hidden = false;
    const hit = log.querySelector('.hit');
    if (hit) hit.parentNode.scrollTop = hit.offsetTop - hit.parentNode.clientHeight / 2;
    close.focus();
  }

  function closeLines() {
    /* Lines that were asked for and have not come are called off as well. */
    opened += 1;
    if (log.hidden) return;
    const within = log.contains(document.activeElement);
    if (within) document.activeElement.blur();
    log.hidden = true;
    log.replaceChildren();
    /* The focus goes back to the mark that opened the lines, and to the title when that mark is
       drawn no more. */
    if (opener && opener.isConnected) {
      /* What is no stop of its table takes no focus. */
      hold(opener);
      opener.focus();
    } else if (within) {
      report.querySelector('h1').focus({ preventScroll: true });
    }
    opener = null;
    excerpt = null;
  }

  /* The mark an arrow key leads to: along its row, or the nearest in the next row that has one. */
  function toward(mark, key) {
    const row = mark.closest('[data-row]');
    /* Along a row by where each mark stands: a timeline's are not drawn in the order of their times. */
    const middle = (each) => each.getBoundingClientRect().left + each.getBoundingClientRect().right;
    const stands = new Map([...row.querySelectorAll(STOPS)].map((each) => [each, middle(each)]));
    const along = row.hasAttribute('data-wraps') ? [...stands.keys()] : [...stands.keys()].sort((one, other) => stands.get(one) - stands.get(other));
    const index = along.indexOf(mark);
    if (key === 'ArrowLeft') return along[index - 1];
    if (key === 'ArrowRight') return along[index + 1];
    if (key === 'Home') return along[0];
    if (key === 'End') return along[along.length - 1];
    const next = { ArrowUp: 'previousElementSibling', ArrowDown: 'nextElementSibling' }[key];
    const left = mark.getBoundingClientRect().left;
    for (let other = next && row[next]; other; other = other[next]) {
      const there = [...other.querySelectorAll(STOPS)];
      /* A row whose marks stand line under line is entered at the end it is come to: its first
         mark from above, its last from below. */
      if (there.length && other.hasAttribute('data-wraps')) return there[key === 'ArrowDown' ? 0 : there.length - 1];
      if (there.length) {
        return there.reduce((best, each) => (Math.abs(each.getBoundingClientRect().left - left) < Math.abs(best.getBoundingClientRect().left - left) ? each : best));
      }
    }
    return undefined;
  }

  report.addEventListener('click', (event) => {
    const rest = event.target.closest('[data-rest]');
    const fold = event.target.closest('[data-unfolds]');
    if (rest || fold) {
      const { view, text } = drawn.report;
      const key = fold && fold.getAttribute('data-unfolds');
      if (rest) whole = whole === view ? null : view;
      else if (!unfolded.delete(key)) unfolded.add(key);
      redraw(report, 'report', view, text, () => conversation(told, Boolean(rest)), rest || fold);
      return;
    }
    const mark = event.target.closest('[data-m]');
    if (!mark) return;
    hold(mark);
    showLines(mark);
  });

  report.addEventListener('keydown', (event) => {
    const mark = event.target.closest(STOPS);
    if (!mark || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'Enter' || event.key === ' ') {
      /* A button answers the two keys of its own. */
      if (!mark.hasAttribute('data-m')) return;
      event.preventDefault();
      showLines(mark);
      return;
    }
    const next = mark.closest('[data-row]') && toward(mark, event.key);
    if (next) {
      event.preventDefault();
      hold(next);
      next.focus();
      /* The focus alone brings into sight what is wholly out of it, and leaves a wide cell cut. */
      next.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
  });

  log.addEventListener('click', (event) => {
    if (event.target.closest('button')) closeLines();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeLines();
  });

  window.addEventListener('hashchange', () => {
    closeLines();
    window.scrollTo(0, 0);
    read();
  });

  /* A read by the clock waits for the one under way: an answer may take longer than the clock's turn. */
  function readOn() {
    if (document.visibilityState === 'visible' && !reading) read();
  }

  document.addEventListener('visibilitychange', readOn);

  setInterval(readOn, POLL);

  read();
})();
