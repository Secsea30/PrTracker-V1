/**
 * PRTracker alert log — the Google Sheet side.
 *
 * Receives each alert PRTracker sends and adds it as a row on the "Alerts"
 * tab: date, time, title, source, link, and the full text of the press
 * release. Also builds a "Summary" tab (totals, per day, per source).
 *
 * Setup is in the instructions that came with this file. In short: paste this
 * into Extensions > Apps Script, set TOKEN below, run `setup` once, then
 * Deploy > New deployment > Web app (Execute as: Me, Who has access: Anyone).
 *
 * TOKEN is a private password shared with the PRTracker server so that only it
 * can add rows. Anyone with the web address but without the token is refused.
 */

const TOKEN = 'PASTE-A-LONG-RANDOM-SECRET-HERE';

const ALERTS_SHEET = 'Alerts';
const SUMMARY_SHEET = 'Summary';
const HEADERS = ['Date', 'Time (GST)', 'Title', 'Source', 'Link', 'Body', 'Notes'];
const LINK_COLUMN = 5;  // used to skip rows that are already in the sheet
const BODY_COLUMN = 6;


/** Run this once, by hand. Safe to run again: it never deletes rows. */
function setup() {
  const ss = SpreadsheetApp.getActive();
  ss.setSpreadsheetTimeZone('Asia/Dubai');

  let alerts = ss.getSheetByName(ALERTS_SHEET);
  if (!alerts) alerts = ss.insertSheet(ALERTS_SHEET, 0);
  alerts.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS])
    .setFontWeight('bold').setBackground('#1E3A5F').setFontColor('#FFFFFF');
  alerts.setFrozenRows(1);
  alerts.getRange('A2:A').setNumberFormat('yyyy-mm-dd');
  alerts.getRange('B2:B').setNumberFormat('hh:mm:ss');
  // Press release text is long: keep it on one line per row (click a cell to read it all).
  alerts.getRange('F2:F').setWrapStrategy(SpreadsheetApp.WrapStrategy.CLIP).setVerticalAlignment('top');
  alerts.setColumnWidth(1, 95);
  alerts.setColumnWidth(2, 90);
  alerts.setColumnWidth(3, 420);
  alerts.setColumnWidth(4, 80);
  alerts.setColumnWidth(5, 240);
  alerts.setColumnWidth(6, 520);
  alerts.setColumnWidth(7, 240);

  let summary = ss.getSheetByName(SUMMARY_SHEET);
  if (!summary) summary = ss.insertSheet(SUMMARY_SHEET, 1);
  summary.clear();
  summary.getRange('A1').setValue('PRTracker — alerts sent').setFontWeight('bold').setFontSize(14);
  summary.getRange('A3:B5').setValues([
    ['Total alerts', '=COUNTA(Alerts!E2:E)'],
    ['Today', '=COUNTIF(Alerts!A2:A, TODAY())'],
    ['Last 7 days', '=COUNTIF(Alerts!A2:A, ">="&(TODAY()-6))'],
  ]);
  summary.getRange('A3:A5').setFontWeight('bold');
  summary.getRange('A7').setValue('By source').setFontWeight('bold');
  summary.getRange('A8').setFormula(
    '=IFERROR(QUERY(Alerts!A2:E, "select D, count(E) where D is not null group by D ' +
    'order by count(E) desc label D \'Source\', count(E) \'Alerts\'", 0), "No alerts yet")');
  summary.getRange('D7').setValue('Per day (latest 30)').setFontWeight('bold');
  summary.getRange('D8').setFormula(
    '=IFERROR(QUERY(Alerts!A2:E, "select A, count(E) where A is not null group by A ' +
    'order by A desc limit 30 label A \'Date\', count(E) \'Alerts\'", 0), "No alerts yet")');
  summary.getRange('D9:D').setNumberFormat('yyyy-mm-dd');
  summary.setColumnWidth(1, 140);
  summary.setColumnWidth(4, 110);
}


/** PRTracker posts here. */
function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);  // two alerts at once must not write over each other
  try {
    let body;
    try {
      body = JSON.parse(e.postData.contents);
    } catch (err) {
      return reply({ ok: false, error: 'unreadable request' });
    }
    if (body.token !== TOKEN) return reply({ ok: false, error: 'unauthorized' });

    const sheet = SpreadsheetApp.getActive().getSheetByName(ALERTS_SHEET);
    if (!sheet) return reply({ ok: false, error: 'no "Alerts" sheet — run setup first' });

    const lastRow = sheet.getLastRow();
    // link -> its row number, for every release already in the sheet
    const rowOf = new Map();
    if (lastRow > 1) {
      sheet.getRange(2, LINK_COLUMN, lastRow - 1, 1).getValues()
        .forEach(function (r, i) { rowOf.set(r[0], i + 2); });
    }

    const fresh = [];
    const inThisRequest = new Set();
    let skipped = 0;
    let filled = 0;
    (body.rows || []).forEach(function (r) {
      if (!r.url || inThisRequest.has(r.url)) { skipped++; return; }  // no link, or listed twice in one request
      inThisRequest.add(r.url);
      if (rowOf.has(r.url)) {
        // Already logged (a retry or a backfill). The one thing we still do is fill in
        // a Body that was empty, e.g. its text couldn't be read the first time.
        const row = rowOf.get(r.url);
        const cell = sheet.getRange(row, BODY_COLUMN);
        if (r.body && !cell.getValue()) {
          cell.setValue(r.body);
          filled++;
        } else {
          skipped++;
        }
        return;
      }
      fresh.push([r.date, r.time, r.title, r.source, r.url, r.body || '', '']);
    });

    if (fresh.length) {
      sheet.getRange(lastRow + 1, 1, fresh.length, HEADERS.length).setValues(fresh);
    }
    return reply({ ok: true, added: fresh.length, skipped: skipped, filled: filled });
  } finally {
    lock.releaseLock();
  }
}


function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
