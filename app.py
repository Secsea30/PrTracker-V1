"""
PRTracker dashboard.

Pages:
  /login   — sign in (one account per team member, see users.json)
  /logout  — clear session
  /        — the dashboard itself: Sport/Comfort toggle, plus placeholder
             sections for change history, health status, and usage
             analytics (built in the next steps)

Run locally with:
    uvicorn app:app --reload --port 8000
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Cookie, FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

import analytics
import auth
import health
import history
import localtime
import mode
import tracked_pages as tracked_pages_store
from mailer import add_recipient, load_recipients, remove_recipient

app = FastAPI()


# ---------------------------------------------------------------------------
# Shared page chrome
# ---------------------------------------------------------------------------

THEME_INIT_SCRIPT = """
<script>
  (function() {
    if (localStorage.getItem('prtracker-theme') === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
    }
  })();
</script>
"""

BASE_STYLE = """
<style>
  * { box-sizing: border-box; }

  :root {
    --bg: #f2f3f5;
    --card-bg: #ffffff;
    --border: #e8e9ec;
    --divider: #f0f1f3;
    --text: #14161a;
    --muted: #6b7078;
    --muted-2: #9599a3;
    --header-bg: #ffffff;
    --soft-bg: #f8f9fa;
    --accent: #1E3A5F;
    --accent-contrast: #ffffff;
    --comfort-bg: #EAF1F8; --comfort-text: #1E3A5F;
    --sport-bg: #FDEEE7; --sport-text: #B24E1F;
    --shadow: 0 1px 2px rgba(20,22,26,0.02);
  }
  :root[data-theme="dark"] {
    --bg: #101216;
    --card-bg: #181a20;
    --border: #262931;
    --divider: #22252c;
    --text: #e8e9ec;
    --muted: #9195a0;
    --muted-2: #6f7480;
    --header-bg: #14161b;
    --soft-bg: #1e2128;
    --accent: #4C7CB0;
    --accent-contrast: #ffffff;
    --comfort-bg: rgba(76,124,176,0.16); --comfort-text: #7FADDA;
    --sport-bg: rgba(178,78,31,0.18); --sport-text: #E0985F;
    --shadow: 0 1px 2px rgba(0,0,0,0.2);
  }

  body {
    background: var(--bg);
    font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    margin: 0;
    color: var(--text);
    -webkit-font-smoothing: antialiased;
  }

  /* --- App shell --- */
  .shell-header {
    background: var(--header-bg); border-bottom: 1px solid var(--border);
    padding: 16px 32px; display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; z-index: 10;
  }
  .brand { display: flex; align-items: center; gap: 8px; }
  .brand-mark {
    width: 26px; height: 26px; border-radius: 7px; background: var(--accent);
    display: flex; align-items: center; justify-content: center;
    color: #fff; font-size: 12px; font-weight: 700;
  }
  .brand-name { font-size: 14px; font-weight: 700; letter-spacing: 0.01em; color: var(--text); }
  .who { display: flex; align-items: center; gap: 14px; font-size: 13px; color: var(--muted); }
  .who a { color: var(--muted); text-decoration: none; }
  .who a:hover { color: var(--text); }
  .theme-toggle {
    width: 30px; height: 30px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--soft-bg); color: var(--muted); cursor: pointer;
    display: flex; align-items: center; justify-content: center; padding: 0;
  }
  .theme-toggle:hover { color: var(--text); }
  .theme-toggle .icon-moon { display: none; }
  :root[data-theme="dark"] .theme-toggle .icon-sun { display: none; }
  :root[data-theme="dark"] .theme-toggle .icon-moon { display: block; }

  .page { max-width: 1080px; margin: 0 auto; padding: 32px 24px 64px; }
  .page-header { margin-bottom: 24px; }
  .page-title { font-size: 24px; font-weight: 700; margin: 0 0 4px; letter-spacing: -0.01em; }
  .page-subtitle { font-size: 14px; color: var(--muted); margin: 0; }

  /* --- Layout grids --- */
  .grid-top { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-bottom: 20px; }
  .grid-top .card, .grid-main .card { height: 100%; display: flex; flex-direction: column; }
  .grid-top .card-body, .grid-main .card-body { flex: 1; }
  .grid-main { display: grid; grid-template-columns: 1.6fr 1fr; gap: 20px; }
  @media (max-width: 760px) {
    .grid-top, .grid-main { grid-template-columns: 1fr; }
  }

  /* --- Card --- */
  .card {
    background: var(--card-bg); border-radius: 14px; border: 1px solid var(--border);
    overflow: hidden; box-shadow: var(--shadow);
  }
  .card-body { padding: 24px; }
  .card-header { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
  .card-icon {
    width: 30px; height: 30px; border-radius: 8px; background: var(--comfort-bg); color: var(--accent);
    display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  }
  .card-title { font-size: 14px; font-weight: 700; color: var(--text); }

  .muted { font-size: 13px; color: var(--muted); }
  .placeholder { font-size: 13px; color: var(--muted-2); font-style: italic; }

  /* --- Pulsing check-frequency icon: speed reflects Comfort vs Sport mode --- */
  .icon-pulse-wrap { position: relative; }
  .icon-pulse-wrap::after {
    content: ''; position: absolute; inset: -3px; border-radius: 9px;
    border: 2px solid var(--accent); opacity: 0;
    animation: pulse-ring var(--pulse-speed, 2.2s) ease-out infinite;
  }
  @keyframes pulse-ring {
    0% { transform: scale(0.85); opacity: 0.55; }
    70% { transform: scale(1.5); opacity: 0; }
    100% { opacity: 0; }
  }
  @media (prefers-reduced-motion: reduce) {
    .icon-pulse-wrap::after { animation: none; }
  }

  /* --- Spinning globe: pages are actively being tracked --- */
  .icon-spin svg { animation: icon-spin 3s linear infinite; transform-origin: 50% 50%; }
  @keyframes icon-spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) {
    .icon-spin svg { animation: none; }
  }

  /* --- Mode / status --- */
  .mode-badge {
    display: inline-block; padding: 4px 10px; border-radius: 6px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
  }
  .comfort { background: var(--comfort-bg); color: var(--comfort-text); }
  .sport { background: var(--sport-bg); color: var(--sport-text); }

  .source-badge {
    display: inline-block; padding: 2px 8px; margin-right: 8px; border-radius: 5px;
    font-size: 10px; font-weight: 700; letter-spacing: 0.03em; text-transform: uppercase;
    color: #ffffff; vertical-align: middle;
  }

  .segmented {
    display: flex; background: var(--bg); border-radius: 10px; padding: 4px; margin-top: 18px; gap: 4px;
  }
  .segmented form { flex: 1; }
  .segmented button {
    width: 100%; padding: 9px 0; border: none; border-radius: 8px; font-size: 13px;
    font-weight: 600; cursor: pointer; background: transparent; color: var(--muted);
  }
  .segmented button.active { background: var(--accent); color: var(--accent-contrast); }
  .segmented button:not(.active):hover { background: var(--border); color: var(--text); }

  .timer-panel { background: var(--soft-bg); border-radius: 10px; padding: 16px; margin-top: 14px; }
  .timer-panel-label { font-size: 11px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted-2); margin-bottom: 8px; }
  .timer-value { font-size: 20px; font-weight: 700; color: var(--text); margin-bottom: 10px; }
  .timer-value.idle { font-size: 14px; font-weight: 500; color: var(--muted); }
  .timer-track { height: 6px; background: var(--border); border-radius: 4px; overflow: hidden; }
  .timer-fill { height: 100%; background: #B24E1F; border-radius: 4px; }

  .page-row { display: flex; align-items: center; justify-content: space-between; padding: 10px 0; border-top: 1px solid var(--divider); gap: 12px; }
  .page-row:first-child { border-top: none; padding-top: 0; }
  .page-row-label { font-size: 13px; font-weight: 600; color: var(--text); }
  .page-row-url { font-size: 12px; color: var(--muted-2); text-decoration: none; word-break: break-all; }
  .page-row-url:hover { color: var(--accent); }

  input[type=email], input[type=password] {
    width: 100%; padding: 10px 12px; margin-top: 6px; margin-bottom: 14px;
    border: 1px solid var(--border); border-radius: 8px; font-size: 14px;
    background: var(--card-bg); color: var(--text);
  }
  input[type=submit] {
    width: 100%; padding: 12px 0; border: none; border-radius: 8px; font-size: 14px;
    font-weight: 600; cursor: pointer; background: var(--accent); color: var(--accent-contrast);
  }
  label { font-size: 13px; color: var(--muted); }
  .error { background: var(--sport-bg); color: var(--sport-text); padding: 10px 14px; border-radius: 8px; font-size: 13px; margin-bottom: 16px; }

  /* --- Change history --- */
  .history-item { padding: 14px 0; border-top: 1px solid var(--divider); }
  .history-item:first-child { border-top: none; padding-top: 0; }
  .history-item a { color: var(--text); font-size: 14px; font-weight: 600; text-decoration: none; }
  .history-item a:hover { color: var(--accent); }
  .history-meta { font-size: 12px; color: var(--muted-2); margin-top: 3px; }

  /* --- Health --- */
  .health-page-block { padding: 14px 0; border-top: 1px solid var(--divider); }
  .health-page-block:first-child { border-top: none; padding-top: 0; }
  .health-page-label { font-size: 13px; font-weight: 700; color: var(--text); margin-bottom: 8px; }
  .health-row { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
  .dot-healthy { background: #1E7A4C; }
  .dot-degraded { background: #C98A1A; }
  .dot-down { background: #B24E1F; }
  .health-status-text { font-size: 15px; font-weight: 700; }
  .health-detail { font-size: 13px; color: var(--muted); margin: 3px 0; }
  .health-error {
    margin-top: 12px; font-size: 12px; color: var(--sport-text); background: var(--sport-bg);
    padding: 10px 14px; border-radius: 8px; word-break: break-word;
  }

  /* --- Analytics --- */
  .stat-row { display: flex; gap: 16px; margin-bottom: 20px; }
  .stat { flex: 1; background: var(--soft-bg); border-radius: 10px; padding: 14px; }
  .stat-number { font-size: 22px; font-weight: 700; color: var(--accent); line-height: 1; }
  .stat-label { font-size: 11px; color: var(--muted-2); margin-top: 6px; }
  .sub-label { font-size: 11px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted-2); margin: 0 0 10px; }
  .user-row { display: flex; justify-content: space-between; font-size: 13px; padding: 7px 0; border-top: 1px solid var(--divider); }
  .user-row:first-child { border-top: none; }
  .activity-item { font-size: 12px; color: var(--muted-2); padding: 5px 0; }

  .watermark {
    position: fixed; bottom: 14px; right: 18px; font-size: 11px; color: var(--muted-2);
    opacity: 0.7; pointer-events: none; user-select: none;
  }

  /* --- Settings --- */
  .back-link { font-size: 13px; color: var(--muted); text-decoration: none; display: inline-block; margin-bottom: 16px; }
  .back-link:hover { color: var(--text); }
  .settings-row {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 12px 0; border-top: 1px solid var(--divider);
  }
  .settings-row:first-child { border-top: none; }
  .settings-row-main { font-size: 14px; font-weight: 500; color: var(--text); }
  .settings-row-sub { font-size: 12px; color: var(--muted-2); margin-top: 2px; word-break: break-all; }
  .btn-remove {
    flex-shrink: 0; width: 26px; height: 26px; border-radius: 7px; border: 1px solid var(--border);
    background: var(--soft-bg); color: var(--muted); cursor: pointer; font-size: 15px; line-height: 1;
    display: flex; align-items: center; justify-content: center; padding: 0;
  }
  .btn-remove:hover { background: var(--sport-bg); color: var(--sport-text); border-color: transparent; }
  .add-form { display: flex; gap: 8px; margin-top: 16px; }
  .add-form input { margin: 0; flex: 1; }
  .add-form button {
    flex-shrink: 0; padding: 0 16px; border: none; border-radius: 8px; font-size: 13px;
    font-weight: 600; cursor: pointer; background: var(--accent); color: var(--accent-contrast);
  }
  .settings-hint { font-size: 12px; color: var(--muted-2); margin: 6px 0 0; }
</style>
"""

THEME_TOGGLE_SCRIPT = """
<script>
  function toggleTheme() {
    const root = document.documentElement;
    if (root.getAttribute('data-theme') === 'dark') {
      root.removeAttribute('data-theme');
      localStorage.setItem('prtracker-theme', 'light');
    } else {
      root.setAttribute('data-theme', 'dark');
      localStorage.setItem('prtracker-theme', 'dark');
    }
  }
</script>
"""

ICON_CLOCK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>'
ICON_PULSE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l2-7 4 14 2-7h6"/></svg>'
ICON_LIST = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>'
ICON_CHART = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-4"/></svg>'
ICON_GLOBE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 4 6 4 9s-1.5 6.3-4 9c-2.5-2.7-4-6-4-9s1.5-6.3 4-9z"/></svg>'
ICON_SUN = '<svg class="icon-sun" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
ICON_MOON = '<svg class="icon-moon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'

# Health-status faces: happy (healthy), queasy (degraded), dead/X-eyes (down).
ICON_FACE_HAPPY = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="9" cy="10" r="1" fill="currentColor" stroke="none"/><circle cx="15" cy="10" r="1" fill="currentColor" stroke="none"/><path d="M8 14c1.5 2 6.5 2 8 0"/></svg>'
ICON_FACE_SICK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="9" cy="10" r="1" fill="currentColor" stroke="none"/><circle cx="15" cy="10" r="1" fill="currentColor" stroke="none"/><path d="M8 15c1-1 2 1 3 0s2-1 3 0 2 1 3 0"/></svg>'
ICON_FACE_DEAD = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M7.5 8.5l3 3M10.5 8.5l-3 3M13.5 8.5l3 3M16.5 8.5l-3 3"/><path d="M9 15.5h6"/></svg>'


def section(icon: str, title: str, body: str, icon_class: str = "", icon_style: str = "") -> str:
    return f"""
    <div class="card">
      <div class="card-body">
        <div class="card-header">
          <div class="card-icon {icon_class}" style="{icon_style}">{icon}</div>
          <div class="card-title">{title}</div>
        </div>
        {body}
      </div>
    </div>
    """


def format_dt(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str)
    return localtime.to_local(dt).strftime("%-d %b %Y, %-I:%M %p GST")


def time_ago(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str)
    seconds = (datetime.now(timezone.utc) - dt).total_seconds()
    if seconds < 90:
        return "less than a minute ago"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    return f"{hours} hr {minutes % 60} min ago"


def _health_level_for(state: dict, tracked_page: dict) -> str:
    """Returns "unknown" | "healthy" | "degraded" | "down" for one tracked
    page's health state — shared by the Health status text and the face icon
    so they always agree. Staleness is judged against that page's own actual
    check interval (mode.interval_for), not the global toggle, since a
    force_sport page like WAM checks faster than Comfort mode implies."""
    if not state.get("last_check_at"):
        return "unknown"

    current_interval = mode.interval_for(tracked_page)
    failures = state.get("consecutive_failures", 0)
    seconds_since_check = (datetime.now(timezone.utc) - datetime.fromisoformat(state["last_check_at"])).total_seconds()
    stale = seconds_since_check > current_interval * 3

    if failures == 0 and not stale:
        return "healthy"
    elif failures > 0 and failures < health.FAILURE_ALERT_THRESHOLD and not stale:
        return "degraded"
    else:
        return "down"


def worst_health_level() -> str:
    """The single icon shown next to the "Health status" title reflects the
    worst level across all tracked pages, so one broken page can't hide
    behind another healthy one there either."""
    pages = tracked_pages_store.load_pages()
    if not pages:
        return "unknown"
    all_health = health.get_all_health()
    levels = {_health_level_for(all_health.get(p["url"], {}), p) for p in pages}
    for level in ("down", "degraded", "unknown"):
        if level in levels:
            return level
    return "healthy"


def render_health() -> str:
    pages = tracked_pages_store.load_pages()
    all_health = health.get_all_health()

    if not any(all_health.get(p["url"], {}).get("last_check_at") for p in pages):
        return '<p class="placeholder">No checks have run yet — this fills in once the scheduler starts.</p>'

    blocks = ""
    for page in pages:
        state = all_health.get(page["url"], {})
        level = _health_level_for(state, page)

        if level == "unknown":
            dot, label = ("dot-degraded", "No checks yet")
        else:
            dot, label = {
                "healthy": ("dot-healthy", "Healthy"),
                "degraded": ("dot-degraded", "Degraded — some checks failing"),
                "down": ("dot-down", "Not running normally"),
            }[level]

        last_success_line = (
            f"Last successful check: {time_ago(state['last_success_at'])}"
            if state.get("last_success_at") else "No successful check recorded yet"
        )
        last_check_line = (
            f"Last check attempt: {time_ago(state['last_check_at'])}"
            if state.get("last_check_at") else "Not checked yet"
        )

        error_html = ""
        if state.get("last_error"):
            error_html = f'<div class="health-error">Last error: {state["last_error"]}</div>'

        blocks += f"""
        <div class="health-page-block">
          <div class="health-page-label">{page['label']}</div>
          <div class="health-row">
            <div class="dot {dot}"></div>
            <div class="health-status-text">{label}</div>
          </div>
          <div class="health-detail">{last_success_line}</div>
          <div class="health-detail">{last_check_line}</div>
          {error_html}
        </div>
        """
    return blocks


def render_history(limit: int = 15) -> str:
    entries = history.load_history()[:limit]
    if not entries:
        return '<p class="placeholder">No changes detected yet — this fills in automatically once PRTracker catches its first new press release.</p>'

    # Older entries were recorded before source_label/badge_color existed —
    # backfill them from the current tracked-pages config (matched by page
    # label) so every row still shows the right WAM/MBZ tag, not a blank one.
    pages_by_label = {p["label"]: p for p in tracked_pages_store.load_pages()}

    items_html = ""
    for entry in entries:
        fallback_page = pages_by_label.get(entry.get("page_label"), {})
        source_label = entry.get("source_label") or fallback_page.get("source_label") or entry.get("page_label", "PRTracker")
        badge_color = entry.get("badge_color") or fallback_page.get("badge_color") or tracked_pages_store.DEFAULT_BADGE_COLOR
        items_html += f"""
        <div class="history-item">
          <span class="source-badge" style="background:{badge_color};">{source_label}</span>
          <a href="{entry['url']}" target="_blank" rel="noopener">{entry['title']}</a>
          <div class="history-meta">
            Detected {format_dt(entry['detected_at'])} &middot; Emailed {format_dt(entry['sent_at'])}
          </div>
        </div>
        """
    return items_html


def render_tracked_pages() -> str:
    pages = tracked_pages_store.load_pages()
    global_interval = mode.get_status()["interval_seconds"] // 60
    rows = ""
    for page in pages:
        filter_badge = f' <span class="mode-badge sport">Only: {page["keyword_filter"]}</span>' if page.get('keyword_filter') else ''
        pace = "always every 2 min" if page.get("force_sport") else f"every {global_interval} min (follows toggle)"
        rows += f"""
        <div class="page-row">
          <div class="page-row-label">{page['label']}{filter_badge}</div>
          <a class="page-row-url" href="{page['url']}" target="_blank" rel="noopener">{page['url']}</a>
          <div class="settings-row-sub" style="margin-top:2px;">{pace}</div>
        </div>
        """
    count = len(pages)
    note = "Currently tracking 1 page." if count == 1 else f"Currently tracking {count} pages."
    return f"""
    {rows}
    <p class="muted" style="margin-top:14px; margin-bottom:0;">{note} <a href="/settings" style="color:var(--accent);">Manage in Settings &rarr;</a></p>
    """


def _display_name(email: str) -> str:
    for user in auth.load_users():
        if user["email"] == email:
            return user["name"]
    return email


EVENT_LABELS = {
    "dashboard_view": "viewed the dashboard",
    "sport_mode_activated": "activated Sport mode",
    "comfort_mode_activated": "switched back to Comfort mode",
    "login": "signed in",
}


def render_analytics() -> str:
    stats = analytics.summary()

    views_html = ""
    for email, count in sorted(stats["views_per_user"].items(), key=lambda x: -x[1]):
        views_html += f'<div class="user-row"><span>{_display_name(email)}</span><span>{count} views</span></div>'
    if not views_html:
        views_html = '<p class="placeholder">No dashboard views recorded yet.</p>'

    activity_html = ""
    for e in stats["recent_events"]:
        label = EVENT_LABELS.get(e["event"], e["event"])
        activity_html += f'<div class="activity-item">{_display_name(e["user"])} {label} &middot; {time_ago(e["at"])}</div>'
    if not activity_html:
        activity_html = '<p class="placeholder">No activity recorded yet.</p>'

    return f"""
    <div class="stat-row">
      <div class="stat">
        <div class="stat-number">{stats['total_views']}</div>
        <div class="stat-label">Dashboard views (all time)</div>
      </div>
      <div class="stat">
        <div class="stat-number">{stats['total_sport_activations']}</div>
        <div class="stat-label">Sport mode activations</div>
      </div>
    </div>
    <p class="sub-label">Views by person</p>
    {views_html}
    <p class="sub-label" style="margin-top:20px;">Recent activity</p>
    {activity_html}
    """


def shell_header(user: dict = None) -> str:
    right = (
        f'<span>{user["name"]}</span><a href="/settings">Settings</a><a href="/logout">Sign out</a>'
        if user else '<span>Private team dashboard</span>'
    )
    return f"""
    <div class="shell-header">
      <div class="brand">
        <div class="brand-mark">PR</div>
        <div class="brand-name">PRTracker</div>
      </div>
      <div class="who">
        {right}
        <button class="theme-toggle" onclick="toggleTheme()" title="Toggle night mode" aria-label="Toggle night mode">
          {ICON_SUN}{ICON_MOON}
        </button>
      </div>
    </div>
    """


def page_shell(body_html: str, title: str = "PRTracker", user: dict = None) -> str:
    return f"""
    <!doctype html>
    <html>
    <head><meta charset="utf-8"><title>{title}</title>{THEME_INIT_SCRIPT}{BASE_STYLE}</head>
    <body>
      {shell_header(user)}
      <div class="page">{body_html}</div>
      <div class="watermark">By Antony Emil</div>
      {THEME_TOGGLE_SCRIPT}
    </body>
    </html>
    """


def get_current_user(session: str | None):
    if not session:
        return None
    return auth.read_session_token(session)


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(error: str = ""):
    error_html = f'<div class="error">{error}</div>' if error else ""
    body = f"""
    <div style="max-width: 380px; margin: 60px auto 0;">
      <div class="card">
        <div class="card-body">
          <div class="page-title" style="font-size:19px; margin-bottom:2px;">Sign in</div>
          <p class="page-subtitle" style="margin-bottom:20px;">Private dashboard — team access only.</p>
          {error_html}
          <form method="post" action="/login">
            <label>Email</label>
            <input type="email" name="email" required autofocus>
            <label>Password</label>
            <input type="password" name="password" required>
            <input type="submit" value="Sign in">
          </form>
        </div>
      </div>
    </div>
    """
    return page_shell(body, "Sign in — PRTracker")


@app.post("/login")
def login_submit(email: str = Form(...), password: str = Form(...)):
    user = auth.verify_login(email, password)
    if not user:
        return RedirectResponse(url="/login?error=Incorrect+email+or+password", status_code=303)

    analytics.log_event("login", user["email"])
    token = auth.create_session_token(user["email"])
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        auth.SESSION_COOKIE_NAME, token,
        max_age=auth.SESSION_MAX_AGE_SECONDS, httponly=True, samesite="lax",
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(prtracker_session: str = Cookie(default=None)):
    user = get_current_user(prtracker_session)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    analytics.log_event("dashboard_view", user["email"])

    status = mode.get_status()
    mode_label = "Sport Mode" if status["mode"] == "sport" else "Comfort Mode"
    mode_class = "sport" if status["mode"] == "sport" else "comfort"

    sport_total_minutes = int(mode.SPORT_MAX_DURATION.total_seconds() // 60)
    if status["mode"] == "sport" and status.get("sport_expires_at"):
        expires = datetime.fromisoformat(status["sport_expires_at"])
        minutes_left = max(0, int((expires - datetime.now(timezone.utc)).total_seconds() // 60))
        percent_remaining = max(0, min(100, round(minutes_left / sport_total_minutes * 100)))
        timer_panel = f"""
        <div class="timer-panel">
          <div class="timer-panel-label">Time remaining in Sport mode</div>
          <div class="timer-value">{minutes_left} min left</div>
          <div class="timer-track"><div class="timer-fill" style="width:{percent_remaining}%;"></div></div>
        </div>
        """
    else:
        timer_panel = f"""
        <div class="timer-panel">
          <div class="timer-panel-label">Time remaining in Sport mode</div>
          <div class="timer-value idle">Not currently active — auto-reverts after {sport_total_minutes} min when on.</div>
          <div class="timer-track"><div class="timer-fill" style="width:0%;"></div></div>
        </div>
        """

    frequency_body = f"""
      <div class="muted">
        Currently <span class="mode-badge {mode_class}">{mode_label}</span> &middot;
        checking every {status['interval_seconds'] // 60} minutes.
      </div>
      <div class="segmented">
        <form method="post" action="/comfort">
          <button type="submit" class="{'active' if status['mode'] == 'comfort' else ''}">Comfort · 10 min</button>
        </form>
        <form method="post" action="/sport">
          <button type="submit" class="{'active' if status['mode'] == 'sport' else ''}">Sport · 2 min</button>
        </form>
      </div>
      {timer_panel}
    """

    health_icon = {
        "unknown": ICON_PULSE,
        "healthy": ICON_FACE_HAPPY,
        "degraded": ICON_FACE_SICK,
        "down": ICON_FACE_DEAD,
    }[worst_health_level()]

    globe_class = "icon-spin" if tracked_pages_store.load_pages() else ""

    body = f"""
    <div class="page-header">
      <div class="page-title">Dashboard</div>
      <p class="page-subtitle">Monitoring {len(tracked_pages_store.load_pages())} pages for new press releases.</p>
    </div>

    <div class="grid-top">
      {section(ICON_CLOCK, "Check frequency", frequency_body, icon_class="icon-pulse-wrap", icon_style=f"--pulse-speed: {'0.5s' if status['mode'] == 'sport' else '2.2s'};")}
      {section(ICON_GLOBE, "Tracked pages", render_tracked_pages(), icon_class=globe_class)}
      {section(health_icon, "Health status", render_health())}
    </div>

    <div class="grid-main">
      {section(ICON_LIST, "Change history", render_history())}
      {section(ICON_CHART, "Usage analytics", render_analytics())}
    </div>
    """
    return page_shell(body, user=user)


@app.post("/sport")
def activate_sport(prtracker_session: str = Cookie(default=None)):
    user = get_current_user(prtracker_session)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    mode.enable_sport_mode()
    analytics.log_event("sport_mode_activated", user["email"])
    return RedirectResponse(url="/", status_code=303)


@app.post("/comfort")
def activate_comfort(prtracker_session: str = Cookie(default=None)):
    user = get_current_user(prtracker_session)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    mode.enable_comfort_mode()
    analytics.log_event("comfort_mode_activated", user["email"])
    return RedirectResponse(url="/", status_code=303)


# ---------------------------------------------------------------------------
# Settings — manage alert recipients and tracked pages without editing code
# ---------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(prtracker_session: str = Cookie(default=None)):
    user = get_current_user(prtracker_session)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    recipients = load_recipients()
    recipient_rows = "".join(f"""
      <div class="settings-row">
        <div class="settings-row-main">{email}</div>
        <form method="post" action="/settings/recipients/remove">
          <input type="hidden" name="email" value="{email}">
          <button class="btn-remove" type="submit" title="Remove {email}">&times;</button>
        </form>
      </div>
    """ for email in recipients)

    pages = tracked_pages_store.load_pages()
    page_rows = "".join(f"""
      <div class="settings-row">
        <div>
          <div class="settings-row-main">{p['label']}{f' <span class="mode-badge sport">Only: {p["keyword_filter"]}</span>' if p.get('keyword_filter') else ''}</div>
          <div class="settings-row-sub">{p['url']}</div>
        </div>
        <form method="post" action="/settings/pages/remove">
          <input type="hidden" name="url" value="{p['url']}">
          <button class="btn-remove" type="submit" title="Remove {p['label']}">&times;</button>
        </form>
      </div>
    """ for p in pages)

    body = f"""
    <a class="back-link" href="/">&larr; Back to dashboard</a>
    <div class="page-header">
      <div class="page-title">Settings</div>
      <p class="page-subtitle">Manage who gets alerts and which pages PRTracker watches — no code editing needed.</p>
    </div>

    <div class="card" style="margin-bottom:20px;">
      <div class="card-body">
        <div class="card-header">
          <div class="card-icon">{ICON_PULSE}</div>
          <div class="card-title">Alert recipients</div>
        </div>
        {recipient_rows}
        <form class="add-form" method="post" action="/settings/recipients/add">
          <input type="email" name="email" placeholder="name@placecomms.com" required>
          <button type="submit">Add</button>
        </form>
        <p class="settings-hint">Everyone on this list gets emailed the moment a new press release is detected.</p>
      </div>
    </div>

    <div class="card">
      <div class="card-body">
        <div class="card-header">
          <div class="card-icon">{ICON_GLOBE}</div>
          <div class="card-title">Tracked pages</div>
        </div>
        {page_rows}
        <form class="add-form" method="post" action="/settings/pages/add">
          <input type="text" name="label" placeholder="Label, e.g. Arabic News" required style="flex:0.5;">
          <input type="url" name="url" placeholder="https://..." required>
          <input type="text" name="keyword_filter" placeholder="Only alert if this word appears (optional)" style="flex:0.8;">
          <button type="submit">Add</button>
        </form>
        <p class="settings-hint">New pages should be another listing page on mohamedbinzayed.ae with the same layout as the current one. A page from a genuinely different site needs its structure investigated first — ask Claude to add it properly rather than adding it here.</p>
      </div>
    </div>
    """
    return page_shell(body, "Settings — PRTracker", user=user)


@app.post("/settings/recipients/add")
def add_recipient_route(prtracker_session: str = Cookie(default=None), email: str = Form(...)):
    if not get_current_user(prtracker_session):
        return RedirectResponse(url="/login", status_code=303)
    add_recipient(email)
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/recipients/remove")
def remove_recipient_route(prtracker_session: str = Cookie(default=None), email: str = Form(...)):
    if not get_current_user(prtracker_session):
        return RedirectResponse(url="/login", status_code=303)
    remove_recipient(email)
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/pages/add")
def add_page_route(
    prtracker_session: str = Cookie(default=None),
    label: str = Form(...),
    url: str = Form(...),
    keyword_filter: str = Form(""),
):
    if not get_current_user(prtracker_session):
        return RedirectResponse(url="/login", status_code=303)
    tracked_pages_store.add_page(label, url, keyword_filter=keyword_filter.strip() or None)
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/pages/remove")
def remove_page_route(prtracker_session: str = Cookie(default=None), url: str = Form(...)):
    if not get_current_user(prtracker_session):
        return RedirectResponse(url="/login", status_code=303)
    tracked_pages_store.remove_page(url)
    return RedirectResponse(url="/settings", status_code=303)
