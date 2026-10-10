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
      const answer = await fetch(path, { signal: stop.signal, cache: 'no-store' });
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
      return { text, said: JSON.parse(text) };
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
      sayWhy({ unreadable: true });
      return;
    }
    answered = new Date();
    say('');
  }

  /* Draws a part of the page anew. A reader of the same view is left where they were in it. */
  function redraw(container, name, view, text, build) {
    const was = drawn[name];
    if (was.view === view && was.text === text) return;
    const started = performance.now();
    const stays = was.view === view;
    const held = stays && container.contains(document.activeElement) ? document.activeElement : null;
    const record = held && recordOf(held);
    const place = stays ? placeIn(container) : null;
    const scrolled = [...container.querySelectorAll('.scroll')].map((box) => [box.scrollLeft, box.scrollTop]);
    /* Each table keeps the stop it had, where that is drawn still. */
    const stood = stays
      ? [...container.querySelectorAll('[data-roving]')].map((group) => stops.get(group)).filter(Boolean).map((stop) => [stop, recordOf(stop)])
      : [];
    const made = build();
    /* A focused element that leaves the document leaves the Tab key going on from where it stood. */
    if (container.contains(document.activeElement)) document.activeElement.blur();
    container.replaceChildren(...made);
    drawn[name] = { view, text };
    container.querySelectorAll('.scroll').forEach((box, index) => {
      if (stays && scrolled[index]) box.scrollTo(...scrolled[index]);
      /* The newest epochs are what a lane is opened for. */
      else if (box.hasAttribute('data-newest')) box.scrollTo(box.scrollWidth, 0);
    });
    for (const group of container.querySelectorAll('[data-roving]')) {
      const first = group.querySelector('[data-m]');
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

  /* The part at the middle of the window, by the name it keeps from one drawing to the next,
     and how far down the window it starts. */
  function placeIn(container) {
    /* Whoever reads log lines is at the mark that opened them, while it is in the window. */
    if (excerpt && opener && container.contains(opener)) {
      const { top, bottom } = opener.getBoundingClientRect();
      if (bottom > 0 && top < window.innerHeight) return { record: excerpt.record, top };
    }
    let found = null;
    for (const made of container.querySelectorAll('[data-key]')) {
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
    let same;
    if (record) same = markOfRecord(container, record);
    else if (place !== null) same = [...container.querySelectorAll('a')].find((made) => made.getAttribute('href') === place);
    else same = [...container.querySelectorAll('[tabindex]')].find((made) => made.tagName === held.tagName && made.getAttribute('aria-label') === name);
    if (same && record) hold(same);
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
    if (was && was !== mark) was.removeAttribute('tabindex');
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

  function conversation(convo) {
    const split = convo.split_epoch !== null;
    const at = {
      split,
      hues: new Map(convo.branches.map((branch, index) => [branch.label, HUES[index] || 'bn'])),
      states: new Map(convo.states.map((state) => [`${state.epoch}:${state.state}`, state])),
      seats: new Map(convo.installations.map((seat) => [seat.id, seat])),
      columns: new Map(convo.epochs.map((epoch, index) => [epoch, index])),
      order: convo.lanes.flatMap((group) => group.installations),
      runs: new Map(convo.runs.map((run) => [run.id, run])),
      marks: [],
    };
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
      section('Installations', 'a row is an installation, a column an epoch, a cell the state it reported there', ...lanes(convo, at)),
      section('Timeline', 'a line is a run, a mark where its installation reached an epoch with the letter of its branch, dots where its delivery was not online; times are UTC', timeline(convo, at)),
      section(split ? 'The state each branch holds' : 'The state the group holds', 'the newest state of each branch, and whom its roster lists', holds(convo, at)),
      ...linesOf(convo.lines),
    ];
    /* Kept once the whole is drawn: a drawing that fails leaves the marks of the one before. */
    marks = at.marks;
    runs = at.runs;
    return made;
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
    for (const { position, count, installations } of convo.positions) {
      made.append(tile(position, count, (POSITIONS.get(position) || { gloss: '' }).gloss, count > NAMED ? [] : installations));
    }
    return made;
  }

  function tile(name, value, gloss, who = []) {
    const made = into(el('div', 'tile'), el('dt', '', name), el('dd', 'v', value), el('dd', 'd', gloss));
    if (who.length) made.append(el('dd', 'who', who.join(', ')));
    return made;
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
      for (const id of group.installations) body.append(lane(at.seats.get(id), convo, at, tail));
    }
    const table = into(el('table', 'lanes'), into(el('thead'), titles), body);
    table.setAttribute('data-roving', '');
    const box = scroller('lanesbox', 'The installations, epoch by epoch', table);
    box.setAttribute('data-newest', '');
    return [
      legend(convo, at),
      el('p', 'quiet', 'A cell that holds a record opens the log lines around it. The arrow keys move between such cells, and Enter opens one.'),
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
    const body = el('tbody');
    for (const id of at.order) {
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
      row.append(into(el('td'), into(el('div', 'plotbox'), strip)));
    }
    const titles = into(el('tr'), heading('', 'Installation'), into(heading('', ''), into(el('div', 'plotbox'), axis)));
    const table = into(el('table', 'times'), into(el('thead'), titles), body);
    table.setAttribute('data-roving', '');
    return scroller('timeline', 'The timeline', table);
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
        chips(branch.installations, at, 'no installation'),
        el('h4', 'subhead', 'Whom its roster lists'),
        chips(newest ? newest.roster : [], at, 'nobody'),
      ));
    }
    if (!convo.branches.length) made.append(el('p', 'quiet', 'No log holds a state of this conversation.'));
    return made;
  }

  function chips(ids, at, none) {
    if (!ids.length) return el('p', 'quiet', none);
    const made = el('ul', 'roster');
    for (const id of ids) {
      const seat = at.seats.get(id);
      const stands = [seat.position];
      if (seat.branch !== null && at.split) stands.push(`on ${seat.branch}`);
      made.append(into(el('li', 'chip'), el('span', 'mono', id), el('em', '', stands.join(', '))));
    }
    return made;
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
    /* The report may have been drawn anew while the lines were asked for. */
    opener = mark.isConnected ? mark : markOfRecord(report, record);
    if (opener) hold(opener);
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
    const along = [...row.querySelectorAll('[data-m]')];
    const index = along.indexOf(mark);
    if (key === 'ArrowLeft') return along[index - 1];
    if (key === 'ArrowRight') return along[index + 1];
    if (key === 'Home') return along[0];
    if (key === 'End') return along[along.length - 1];
    const next = { ArrowUp: 'previousElementSibling', ArrowDown: 'nextElementSibling' }[key];
    const left = mark.getBoundingClientRect().left;
    for (let other = next && row[next]; other; other = other[next]) {
      const there = [...other.querySelectorAll('[data-m]')];
      if (there.length) {
        return there.reduce((best, each) => (Math.abs(each.getBoundingClientRect().left - left) < Math.abs(best.getBoundingClientRect().left - left) ? each : best));
      }
    }
    return undefined;
  }

  report.addEventListener('click', (event) => {
    const mark = event.target.closest('[data-m]');
    if (!mark) return;
    hold(mark);
    showLines(mark);
  });

  report.addEventListener('keydown', (event) => {
    const mark = event.target.closest('[data-m]');
    if (!mark || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      showLines(mark);
      return;
    }
    const next = toward(mark, event.key);
    if (next) {
      event.preventDefault();
      hold(next);
      next.focus();
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
