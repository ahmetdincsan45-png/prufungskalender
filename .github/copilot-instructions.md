# AI Coding Agent Instructions for Prüfungskalender

This repo is a Flask + SQLite web app for managing class exams with a FullCalendar UI and a small admin area. Use these notes to be productive quickly and safely in this codebase.

## Big Picture
- Backend: Flask app in [app.py](app.py), SQLite DB selected by `SQLITE_DB_PATH` or falls back to `/var/data/prufungskalender.db` (Render) or `/tmp/prufungskalender.db`.
- Frontend: Jinja templates in [templates/index.html](templates/index.html), [templates/add.html](templates/add.html), [templates/delete.html](templates/delete.html). FullCalendar + Bootstrap.
- Biometric auth: Optional face recognition login using [face-api.js](https://github.com/justadudewhohacks/face-api.js) in [static/face.js](static/face.js); face descriptors stored in browser localStorage.
- Data model: tables `exams`, `visits`, `subjects`, `admin_credentials` created on first run; see initializers in [app.py](app.py#L229-L309).
- Holidays: Background ranges merged into calendar from external APIs with caching and local JSON fallbacks in `/var/data/ferien_cache`, `/var/data/feiertage_cache`, `/var/data/ferien_fallback`, seeded from [ferien_fallback_seed/](ferien_fallback_seed).

## Run, Build, Deploy
- Local dev:
  - Create venv and install deps from [requirements.txt](requirements.txt).
  - Run with `python app.py` (dev server prints DB path). Health check at [/health](app.py#L735-L746).
- Render deploy: see [render.yaml](render.yaml): `gunicorn app:app`, persistent disk at `/var/data`, `PYTHON_VERSION=3.11`, `SQLITE_DB_PATH=/var/data/prufungskalender.db`.

## Routes & Behaviors
- [/](app.py#L354-L373): Renders next upcoming exam; "after 18:00" cutoff uses Europe/Berlin if `zoneinfo` available.
- [/events](app.py#L373-L597): Returns exam events and weekday-only background ranges for holidays.
  - Event shape: `{id,title,start,end,backgroundColor,borderColor}`; past exams colored `#dc3545` (red), future `#007bff` (blue).
  - Background ranges use `{start,end,rendering:'background',display:'background',backgroundColor:'#f0f0f0'}` and skip weekends.
- [/add](app.py#L597-L639): Accepts `subjects` (comma-separated) and `date` (`YYYY-MM-DD`). Past dates are redirected to index. Inserts one `exams` row per subject.
- [/delete](app.py#L639-L687): Lists future exams plus last 10 past; allows deleting future exams only from this page.
- Admin area (requires session-based login at [/stats/login](app.py#L63-L157)):
  - [/stats](app.py#L771-L1521): Flask session auth (`session['stats_authed']`). Manage `subjects`, view visits, send report, logout.
  - [/stats/logout](app.py#L185-L189): Clear session and redirect to login.
  - [/stats/verify-credentials](app.py#L157-L185): POST endpoint for biometric registration; validates username/password before allowing face capture.
  - [/stats/delete-past](app.py#L687-L735): Delete past exams (requires `@login_required` decorator checking session).
  - [/stats/subjects/add](app.py), [/stats/subjects/delete](app.py): Manage subject pool from stats page.
  - [/send-report](app.py): Sends weekly email report (uses Gmail SMTP settings in [app.py](app.py#L29-L33)).
  - [/admin/reset](app.py), [/admin/info](app.py): Protected by env tokens (`ADMIN_RESET_TOKEN`, `ADMIN_INFO_TOKEN`).

## Conventions & Patterns
- Dates: `YYYY-MM-DD` strings; times default to `08:00`/`16:00` if not provided. Jinja `strftime` filter in [app.py](app.py#L1916-L1936).
- Grade defaults to `4A`; subject lists merge defaults and DB (see `/api/subjects` in [app.py](app.py#L746-L771)).
- Requests to external APIs (`ferien-api.de`, `date.nager.at`) have short timeouts; cached JSONs are used if online fetch fails; local fallbacks loaded from [ferien_fallback_seed](ferien_fallback_seed).
- Visit logging: every non-static request stores `ip`, `user_agent`, `path` into `visits` unless bot-like; deduplicates same IP within 7 days; your changes should preserve this lightweight logging.
- Auth: Stats auth is Flask session-based (`session['stats_authed']`); `@login_required` decorator wraps protected routes. Helper `get_admin_credentials()` returns `(username, password_hash)` from `admin_credentials` table.
- Biometric login: [static/face.js](static/face.js) uses face-api.js models from CDN; face descriptors stored in browser localStorage; server validates credentials via `/stats/verify-credentials` before allowing face registration.

## Developer Workflows
- Initialize DB: done once via `init_db()` before requests; prints the resolved `DB_PATH`. Use `/health` to verify.
- Adding features:
  - Add new routes in [app.py](app.py) and corresponding UI in [templates](templates). Keep JSON shapes and calendar background behavior consistent with `/events`.
  - If new data columns are needed, update `CREATE TABLE IF NOT EXISTS` clauses and any SELECTs emitting JSON for the calendar or admin.
  - When adding holiday sources, follow the cache+fallback pattern in `/events`.
- Email: SMTP credentials are currently hardcoded in [app.py](app.py#L29-L33). Prefer environment variables for new integrations.
- Protected routes: Use `@login_required` decorator above route definition; checks `session.get('stats_authed')` and redirects to login if False.

## Known Mismatches with README
- README lists `/export.csv` and class filters not present in [app.py](app.py). Treat README "features" as legacy/aspirational; trust current routes in code.

## Example Changes
- CSV export: implement `GET /export.csv` emitting columns `date,start_time,end_time,grade,subject` from `exams`. Link a "CSV Dışa Aktar" button in [templates/index.html](templates/index.html) if desired.

Use `/health` and the dev server logs to validate DB path and WAL mode after changes. Keep edits minimal, follow existing patterns, and maintain caching + fallbacks for reliability.
