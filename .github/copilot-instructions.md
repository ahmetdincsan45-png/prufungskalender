# AI Coding Agent Instructions for Prüfungskalender

A Flask + SQLite web app for managing Bavarian school exams with FullCalendar UI, admin stats area, and offline support. Designed for Render deployment with persistent disk.

## Big Picture
- **Backend**: [app.py](app.py) (2400+ lines); initializes all tables on first request via `@app.before_request` hook.
- **Database**: SQLite with WAL mode, automatic fallback path logic: `SQLITE_DB_PATH` env > `/var/data/prufungskalender.db` (Render) > `/tmp/prufungskalender.db` (dev).
- **Tables**: `exams` (id, subject, grade, date, start_time, end_time); `visits` (IP dedup within 7 days); `subjects` (teacher-managed pool); `admin_credentials`; `email_schedule`.
- **Frontend**: [templates/index.html](templates/index.html), [add.html](templates/add.html), [delete.html](templates/delete.html)—Jinja + FullCalendar + Bootstrap.
- **Holidays**: Background ranges from external APIs (`ferien-api.de`, `date.nager.at`) cached to disk, fallback to local JSON in [ferien_fallback_seed/](ferien_fallback_seed), weekday-only rendering.

## Initialization & Environment
- **First Request**: `@app.before_request` calls `init_db()` once (guarded by `_init_lock`); prints resolved `DB_PATH` to console.
- **Seed Fallback**: `seed_fallback_if_needed()` copies `ferien_fallback_seed/BY_*.json` to persistent disk on first run—idempotent, one-time operation.
- **Database Config**: WAL mode, `synchronous=NORMAL`, `busy_timeout=5s`, `foreign_keys=ON`, `check_same_thread=False` (safe for gunicorn).
- **Path Resolution**: `DB_PATH` follows env → prod disk → dev temp; creates `DATA_DIR` and cache directories automatically.
- **Visit Logging**: Lightweight background tracking—every non-static, non-bot request stores IP/UA/path once per IP per 7 days (dedup on `@app.before_request`).

## Critical Routes
- [/](app.py#L306-L323): Index page; renders next upcoming exam with "after 18:00" cutoff using Europe/Berlin timezone when `zoneinfo` available.
- [/events](app.py#L325-L547): JSON calendar events + weekday-only holiday backgrounds; past exams colored red (#dc3545), future blue (#007bff).
- [/add](app.py#L549-L589): Add exam(s); accepts comma-separated subjects + YYYY-MM-DD date, redirects past dates to index, inserts one row per subject.
- [/delete](app.py#L591-L637): List future exams + last 10 past; allows delete only for future exams.
- [/health](app.py#L687-L697): DB health check; returns JSON with DB_PATH and WAL status.
- [/stats](app.py#L723-L1980): Admin dashboard (session auth); manage subjects, view visits, send reports, logout.
- [/send-report](app.py#L2247-L2276): Manual weekly email trigger (SMTP: ahmetdincsan45@gmail.com, pass from app.py:L29-L33).
- [/admin/reset](app.py#L1988-L2013) & [/admin/info](app.py#L2038-L2056): Protected by env tokens (`ADMIN_RESET_TOKEN`, `ADMIN_INFO_TOKEN`).

## Conventions & Patterns
- **Dates & Times**: All dates as `YYYY-MM-DD` strings; times default to `08:00`/`16:00` if not provided.
- **Grade Default**: Defaults to `4A`; subject list merges hardcoded defaults `['Mathematik','Deutsch','HSU','Englisch','Ethik','Religion','Musik']` with DB pool (see [/api/subjects](app.py#L698-L721)).
- **External APIs**: Requests to `ferien-api.de` and `date.nager.at` have short timeouts; cached JSONs on disk used if fetch fails; local [ferien_fallback_seed/](ferien_fallback_seed) loaded as final fallback.
- **Visit Logging**: Non-static, non-bot requests trigger lightweight IP/UA/path logging with 7-day dedup (see [before_request](app.py#L270-L299)).
- **Auth**: Session-based (`session['stats_authed']`); use `@login_required` decorator on protected routes; `get_admin_credentials()` returns username and password_hash from DB.
- **Calendar Events**: JSON format `{id,title,start,end,backgroundColor,borderColor}`; background ranges use `{start,end,rendering:'background',display:'background',backgroundColor:'#f0f0f0'}` with weekday-only filtering.

## Developer Workflows
- **Initialize DB**: Done automatically on first request via `@app.before_request`; prints resolved `DB_PATH`. Use [/health](app.py#L687-L697) to verify DB and WAL status.
- **Local Development**: Run `python app.py`; check console output for DB path confirmation.
- **Adding Features**:
  - Add new routes in [app.py](app.py) and corresponding UI in [templates/](templates). Keep JSON event shapes and calendar behavior consistent with `/events`.
  - When adding DB columns, update `CREATE TABLE IF NOT EXISTS` clauses and any SELECTs emitting JSON for calendar or admin views.
  - Follow cache+fallback pattern for new API integrations (see holiday fetching in `/events`).
- **Email Configuration**: SMTP credentials hardcoded at [app.py:L29-L33](app.py#L29-L33); migrate to env vars for production use.
- **Protected Routes**: Use `@login_required` decorator; checks `session.get('stats_authed')` and redirects to login if missing.

## Known Mismatches with README
- README lists `/export.csv` and class filters not present in [app.py](app.py). Treat README "features" as legacy/aspirational; trust current routes in code.

## Example Changes
- **CSV export**: Implement `GET /export.csv` emitting columns `date,start_time,end_time,grade,subject` from `exams`. Link a "CSV Dışa Aktar" button in [templates/index.html](templates/index.html) if desired.

Use [/health](app.py#L687-L697) and dev server logs to validate DB path and WAL mode after changes. Keep edits minimal, follow existing patterns, and maintain caching + fallbacks for reliability.
