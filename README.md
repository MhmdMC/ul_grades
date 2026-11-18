# ULFG Grade Monitor

Flask application for monitoring Lebanese University Faculty of Engineering grades,
with SQLite storage, Socket.IO alerts, semester history, sharing, and rankings.

## Run

Install `requirements.txt`, set a persistent `SECRET_KEY`, and run `python app.py`.
The default port is 5000 (`PORT` overrides it). Keep the same secret when upgrading:
saved ULFG cookies and credentials are encrypted with it.

## Semesters

- The dashboard defaults to the current class returned by ULFG's current-class
  endpoint, with the existing classes-list fallback if that endpoint is unavailable.
- Dashboard visits discover enrollments at most once every 24 hours and fetch only
  current-semester grades when needed. Students cannot import historical grades.
- **Admin → Semesters** has refresh buttons for everyone's history, one student's
  history, and one specific previous semester. Bulk and student refreshes also
  rediscover enrollments. Imports run in the background with persistent progress
  and safe error messages. Only one history job runs at a time; a job interrupted
  by a server restart can be retried after ten minutes without progress.
- Dashboard, sharing settings, and student profiles have a semester selector.
  Rankings include historical classes; directory entries link to the exact shared
  semester. Search opens a student's current profile, where history is selectable.
- Grades, averages, and sharing choices are stored separately for each user and
  class ID. Repeated courses cannot overwrite another semester's grades.
- New semesters are private by default. Existing self-access, anonymous ranking,
  and admin-consent rules apply independently to each semester's grade fields.
  Admin imports do not grant admins additional permission to view private grades.
- Live polling and alerts follow the current semester. Viewing history does not
  change monitoring. Failed imports preserve saved grades and can be retried.
- Classes from different academic years are kept separate. English/French sections
  are grouped when the API supplies matching academic-year metadata; unknown
  academic years remain separate to avoid combining unrelated cohorts.

## Upgrade and data

Back up `ul_grades.sqlite3` before upgrading. Startup automatically copies legacy
grades, averages, and sharing choices into semester-scoped tables, using each
student's stored class ID. The migration runs once and leaves the original tables
for recovery. Account deletion also removes that account's legacy rows.

The migration cannot recover semesters already overwritten by the old application;
those are imported from ULFG when available. Existing database contents and ULFG
credentials must never be committed to Git.

`DATABASE_PATH` selects an alternate SQLite file. Set
`DISABLE_BACKGROUND_SERVICES=1` to disable scheduled polling in isolated tests.
The default scheduled polling window remains 04:00–12:59 UTC.

## Tests

Install `pytest` and run `python -m pytest tests -q`. Tests use a temporary SQLite
database and mocked ULFG responses; they do not access the local user database.

The backend is mostly in `app.py`; `ul_api.py` wraps ULFG requests and
`crypto_utils.py` handles encrypted values. Jinja templates live in `templates/`,
with browser behavior in `static/js/app.js` and styling in `static/css/app.css`.
