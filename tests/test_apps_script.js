// Runs sheets/apps_script.gs against a tiny fake of Google's spreadsheet services.
// Usage: node tests/test_apps_script.js
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');

function makeEnv() {
  const store = { rows: [['Date', 'Time (GST)', 'Title', 'Source', 'Link', 'Body', 'Notes']], locks: 0 };
  store.maxRows = 1000; store.rules = []; store.heights = null; store.inserted = 0;
  const sheet = {
    getLastRow: () => store.rows.length,
    getMaxRows: () => store.maxRows,
    insertRowsAfter: (after, n) => { store.inserted += n; store.maxRows += n; },
    setConditionalFormatRules: rules => { store.rules = rules; },
    setRowHeightsForced: (start, n, h) => { store.heights = { start, n, h }; },
    getRange: (row, col, numRows, numCols) => ({
      getValues: () => store.rows.slice(row - 1, row - 1 + numRows).map(r => r.slice(col - 1, col - 1 + (numCols || 1))),
      setValues: vals => {
        vals.forEach((v, i) => {
          if (!store.rows[row - 1 + i]) store.rows[row - 1 + i] = [];
          v.forEach((cell, j) => { store.rows[row - 1 + i][col - 1 + j] = cell; });  // only the cells in the range, like Sheets
        });
      },
      getValue: () => (store.rows[row - 1] || [])[col - 1],
      setValue: v => { store.rows[row - 1][col - 1] = v; },
    }),
  };
  const book = { getSheetByName: name => (name === 'Alerts' && !store.noSheet ? sheet : null) };
  const env = {
    JSON, Set, Array, Object, console,
    LockService: { getScriptLock: () => ({ waitLock: () => { store.locks++; }, releaseLock: () => { store.locks--; } }) },
    ContentService: {
      MimeType: { JSON: 'json' },
      createTextOutput: text => ({ text, setMimeType() { return this; } }),
    },
    SpreadsheetApp: {
      newConditionalFormatRule: () => {
        const rule = {};
        const b = { whenFormulaSatisfied: f => { rule.formula = f; return b; }, setBackground: c => { rule.colour = c; return b; },
                    setRanges: r => { rule.ranges = r; return b; }, build: () => rule };
        return b;
      },
      getActive: () => (store.standalone ? null : book),
      openById: id => { store.openedId = id; return store.noFallback ? null : book; },
    },
  };
  vm.createContext(env);
  const code = fs.readFileSync(path.join(__dirname, '..', 'sheets', 'apps_script.gs'), 'utf8')
    .replace("const TOKEN = 'PASTE-A-LONG-RANDOM-SECRET-HERE';", "const TOKEN = 'secret';");
  vm.runInContext(code + '\nthis.doPost = doPost; this.applyFormatting = applyFormatting;', env);
  const format = () => env.applyFormatting();
  const post = body => JSON.parse(env.doPost({ postData: { contents: typeof body === 'string' ? body : JSON.stringify(body) } }).text);
  return { post, store, format };
}

const row = (n, extra) => Object.assign({ date: '2026-10-08', time: '14:38:17', title: 'Title ' + n, source: 'WAM', url: 'https://x/' + n, body: 'Body ' + n }, extra);
const same = (a, b) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b));  // arrays made inside the fake Google sandbox have a different prototype, so compare as JSON
let passed = 0;
function test(name, fn) { fn(); passed++; console.log('  ok  ' + name); }

test('rejects a wrong token and writes nothing', () => {
  const { post, store } = makeEnv();
  assert.deepStrictEqual(post({ token: 'nope', rows: [row(1)] }), { ok: false, error: 'unauthorized' });
  assert.strictEqual(store.rows.length, 1);
});
test('rejects a request with no token', () => {
  const { post, store } = makeEnv();
  assert.strictEqual(post({ rows: [row(1)] }).ok, false);
  assert.strictEqual(store.rows.length, 1);
});
test('rejects unreadable input without crashing', () => {
  const { post } = makeEnv();
  assert.deepStrictEqual(post('not json'), { ok: false, error: 'unreadable request' });
});
test('adds a row in the right column order', () => {
  const { post, store } = makeEnv();
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(1)] }), { ok: true, added: 1, skipped: 0, filled: 0 });
  same(store.rows[1], ['2026-10-08', '14:38:17', 'Title 1', 'WAM', 'https://x/1', 'Body 1', '']);
});
test('skips a link that is already in the sheet (retry / backfill safe)', () => {
  const { post, store } = makeEnv();
  post({ token: 'secret', rows: [row(1)] });
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(1), row(2)] }), { ok: true, added: 1, skipped: 1, filled: 0 });
  assert.strictEqual(store.rows.length, 3);
});
test('skips duplicates inside one request', () => {
  const { post, store } = makeEnv();
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(1), row(1)] }), { ok: true, added: 1, skipped: 1, filled: 0 });
  assert.strictEqual(store.rows.length, 2);
});
test('keeps order for a batch and appends below existing rows', () => {
  const { post, store } = makeEnv();
  post({ token: 'secret', rows: [row(1)] });
  post({ token: 'secret', rows: [row(2), row(3), row(4)] });
  same(store.rows.slice(1).map(r => r[2]), ['Title 1', 'Title 2', 'Title 3', 'Title 4']);
});
test('a typed Notes entry on an existing row is left alone', () => {
  const { post, store } = makeEnv();
  post({ token: 'secret', rows: [row(1)] });
  store.rows[1][6] = 'Shared on LinkedIn';
  post({ token: 'secret', rows: [row(2)] });
  assert.strictEqual(store.rows[1][6], 'Shared on LinkedIn');
});
test('still dedupes after the team re-sorts the sheet', () => {
  const { post, store } = makeEnv();
  post({ token: 'secret', rows: [row(1), row(2), row(3)] });
  store.rows = [store.rows[0]].concat(store.rows.slice(1).reverse());
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(2), row(4)] }), { ok: true, added: 1, skipped: 1, filled: 0 });
});
test('ignores rows with no link', () => {
  const { post, store } = makeEnv();
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(1, { url: '' })] }), { ok: true, added: 0, skipped: 1, filled: 0 });
  assert.strictEqual(store.rows.length, 1);
});
test('an empty batch is fine', () => {
  const { post } = makeEnv();
  assert.deepStrictEqual(post({ token: 'secret', rows: [] }), { ok: true, added: 0, skipped: 0, filled: 0 });
});
test('fills in a Body that was empty, on a re-send of the same release', () => {
  const { post, store } = makeEnv();
  post({ token: 'secret', rows: [row(1, { body: '' })] });
  assert.strictEqual(store.rows[1][5], '');
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(1, { body: 'Now readable' })] }), { ok: true, added: 0, skipped: 0, filled: 1 });
  assert.strictEqual(store.rows[1][5], 'Now readable');
});
test('never overwrites a Body that is already there (or one the team edited)', () => {
  const { post, store } = makeEnv();
  post({ token: 'secret', rows: [row(1)] });
  store.rows[1][5] = 'Edited by the team';
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(1, { body: 'Different text' })] }), { ok: true, added: 0, skipped: 1, filled: 0 });
  assert.strictEqual(store.rows[1][5], 'Edited by the team');
});
test('an empty re-send does not blank or change anything', () => {
  const { post, store } = makeEnv();
  post({ token: 'secret', rows: [row(1)] });
  post({ token: 'secret', rows: [row(1, { body: '' })] });
  assert.strictEqual(store.rows[1][5], 'Body 1');
});
test('a release with no text still gets its row, with an empty Body', () => {
  const { post, store } = makeEnv();
  assert.strictEqual(post({ token: 'secret', rows: [row(1, { body: undefined })] }).added, 1);
  assert.strictEqual(store.rows[1][5], '');
});
test('multi-paragraph text is stored intact in one cell', () => {
  const { post, store } = makeEnv();
  post({ token: 'secret', rows: [row(1, { body: 'Para one.\n\nPara two.' })] });
  assert.strictEqual(store.rows[1][5], 'Para one.\n\nPara two.');
});
test('works when the script was created separately and is not attached to the sheet', () => {
  const { post, store } = makeEnv();
  store.standalone = true;
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(1)] }), { ok: true, added: 1, skipped: 0, filled: 0 });
  assert.strictEqual(store.openedId, '1F7-I4yHywDX8tN5P5ybyzuU8GoG85PcUz08Zf2TP2kI');
  assert.strictEqual(store.rows.length, 2);
});
test('says so clearly if it can reach no sheet at all', () => {
  const { post, store } = makeEnv();
  store.standalone = true; store.noFallback = true;
  const reply = post({ token: 'secret', rows: [row(1)] });
  assert.strictEqual(reply.ok, false);
  assert.ok(/not attached/.test(reply.error));
});
test('prefers the attached sheet and does not open by id when attached', () => {
  const { post, store } = makeEnv();
  post({ token: 'secret', rows: [row(1)] });
  assert.strictEqual(store.openedId, undefined);
});
test('formatting: colours WAM blue and MBZ light purple by the Source cell', () => {
  const { format, store } = makeEnv();
  format();
  const byFormula = {}; store.rules.forEach(r => { byFormula[r.formula] = r.colour; });
  assert.strictEqual(byFormula['=$D2="WAM"'], '#B7D7F0');
  assert.strictEqual(byFormula['=$D2="MBZ Site"'], '#E1D5F0');
  assert.strictEqual(store.rules.length, 2);
});
test('formatting: keeps every row one line tall, including future rows', () => {
  const { format, store } = makeEnv();
  format();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(store.heights)), { start: 2, n: 9999, h: 21 });
});
test('formatting: adds room for years of alerts, only once', () => {
  const { format, store } = makeEnv();
  format(); assert.strictEqual(store.maxRows, 10000);
  format(); assert.strictEqual(store.inserted, 9000);
});
test('formatting: corrects old Source names and leaves everything else alone', () => {
  const { post, format, store } = makeEnv();
  post({ token: 'secret', rows: [row(1, { source: 'Latest News' }), row(2, { source: 'WAM - UAE President' }), row(3, { source: 'WAM' }), row(4, { source: 'MBZ Site' })] });
  store.rows[1][6] = 'my note';
  format();
  same(store.rows.slice(1).map(r => r[3]), ['MBZ Site', 'WAM', 'WAM', 'MBZ Site']);
  assert.strictEqual(store.rows[1][6], 'my note');
  assert.strictEqual(store.rows[1][5], 'Body 1');
});
test('reports a missing Alerts sheet clearly', () => {
  const { post, store } = makeEnv();
  store.noSheet = true;
  assert.strictEqual(post({ token: 'secret', rows: [row(1)] }).ok, false);
});
test('always releases the lock, even on a rejected request', () => {
  const { post, store } = makeEnv();
  post({ token: 'bad', rows: [] });
  post('not json');
  post({ token: 'secret', rows: [row(1)] });
  assert.strictEqual(store.locks, 0);
});
console.log(`\n${passed} passed`);
