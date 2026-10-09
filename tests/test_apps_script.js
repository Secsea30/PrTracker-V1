// Runs sheets/apps_script.gs against a tiny fake of Google's spreadsheet services.
// Usage: node tests/test_apps_script.js
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');

function makeEnv() {
  const store = { rows: [['Date', 'Time (GST)', 'Title', 'Source', 'Link', 'Notes']], locks: 0 };
  const sheet = {
    getLastRow: () => store.rows.length,
    getRange: (row, col, numRows, numCols) => ({
      getValues: () => store.rows.slice(row - 1, row - 1 + numRows).map(r => r.slice(col - 1, col - 1 + (numCols || 1))),
      setValues: vals => { vals.forEach((v, i) => { store.rows[row - 1 + i] = v.slice(); }); },
    }),
  };
  const env = {
    JSON, Set, Array, Object, console,
    LockService: { getScriptLock: () => ({ waitLock: () => { store.locks++; }, releaseLock: () => { store.locks--; } }) },
    ContentService: {
      MimeType: { JSON: 'json' },
      createTextOutput: text => ({ text, setMimeType() { return this; } }),
    },
    SpreadsheetApp: { getActive: () => ({ getSheetByName: name => (name === 'Alerts' && !store.noSheet ? sheet : null) }) },
  };
  vm.createContext(env);
  const code = fs.readFileSync(path.join(__dirname, '..', 'sheets', 'apps_script.gs'), 'utf8')
    .replace("const TOKEN = 'PASTE-A-LONG-RANDOM-SECRET-HERE';", "const TOKEN = 'secret';");
  vm.runInContext(code + '\nthis.doPost = doPost;', env);
  const post = body => JSON.parse(env.doPost({ postData: { contents: typeof body === 'string' ? body : JSON.stringify(body) } }).text);
  return { post, store };
}

const row = (n, extra) => Object.assign({ date: '2026-10-08', time: '14:38:17', title: 'Title ' + n, source: 'WAM', url: 'https://x/' + n }, extra);
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
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(1)] }), { ok: true, added: 1, skipped: 0 });
  same(store.rows[1], ['2026-10-08', '14:38:17', 'Title 1', 'WAM', 'https://x/1', '']);
});
test('skips a link that is already in the sheet (retry / backfill safe)', () => {
  const { post, store } = makeEnv();
  post({ token: 'secret', rows: [row(1)] });
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(1), row(2)] }), { ok: true, added: 1, skipped: 1 });
  assert.strictEqual(store.rows.length, 3);
});
test('skips duplicates inside one request', () => {
  const { post, store } = makeEnv();
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(1), row(1)] }), { ok: true, added: 1, skipped: 1 });
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
  store.rows[1][5] = 'Shared on LinkedIn';
  post({ token: 'secret', rows: [row(2)] });
  assert.strictEqual(store.rows[1][5], 'Shared on LinkedIn');
});
test('still dedupes after the team re-sorts the sheet', () => {
  const { post, store } = makeEnv();
  post({ token: 'secret', rows: [row(1), row(2), row(3)] });
  store.rows = [store.rows[0]].concat(store.rows.slice(1).reverse());
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(2), row(4)] }), { ok: true, added: 1, skipped: 1 });
});
test('ignores rows with no link', () => {
  const { post, store } = makeEnv();
  assert.deepStrictEqual(post({ token: 'secret', rows: [row(1, { url: '' })] }), { ok: true, added: 0, skipped: 1 });
  assert.strictEqual(store.rows.length, 1);
});
test('an empty batch is fine', () => {
  const { post } = makeEnv();
  assert.deepStrictEqual(post({ token: 'secret', rows: [] }), { ok: true, added: 0, skipped: 0 });
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
