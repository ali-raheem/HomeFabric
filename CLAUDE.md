# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

HomeFabric is a self-hosted home asset and maintenance manager. It implements
`Product-Specification.md`, which is the source of truth for behaviour; acceptance scenarios
A01–A16 (§16) map onto tests. Note: `/home/ali/Workspace/CLAUDE.md` (parent directory)
describes an unrelated Rust project and does not apply here.

## Commands

```bash
uv sync                                        # install (Python ≥3.11, Django 5.2 LTS)
export FABRIC_DATA_DIR=./data FABRIC_DEBUG=1   # ALWAYS set a data dir; the default is the OS app-data dir
uv run python manage.py migrate
uv run python manage.py runserver              # dev; production is `manage.py serve` (Waitress)
uv run python manage.py run_scheduler          # runs both scheduler jobs every minute

uv run pytest -m "not slow"                    # functional suite (~1 min)
uv run pytest tests/test_recurrence.py::test_leap_day_annual   # single test
uv run pytest -m slow -s                       # performance envelope (10k items / 100k history)
uv run ruff check .
uv run python manage.py makemigrations --check --dry-run
```

Tests use `tests/settings.py`, which creates a temporary `FABRIC_DATA_DIR`. Backup/restore
tests run the real CLI in subprocesses against separate data directories.

## Architecture

A modular Django monolith in `homefabric/`. Apps: `accounts`, `assets`, `documents`,
`maintenance`, `notifications`, `operations`, `manual`, plus `core` for shared helpers.

- **Business rules live in each app's `services.py`.** Views and management commands are
  thin and call services. Services raise `core.errors.DomainError` (the message is safe to
  show users). Edits use optimistic concurrency via `core.concurrency.versioned_update`:
  the model has a `version` field and the form has a hidden `version` input.
- **Household scoping.** `accounts.middleware.HouseholdMiddleware` sets `request.household`
  and `request.role` (viewer/editor/owner, or `admin` for instance administrators, who are
  superusers). Views use the `@household_view("view"|"edit"|"manage"|"export")` decorator
  and load objects with `permissions.scoped()`, so another household's UUID gives a 404.
  Services also check that related objects belong to the same household.
- **Relationships** live in `assets.EntityRelationship` (`located_in`, `part_of`,
  `associated`, `covers`). Validation and cycle checks are in
  `assets/services.py::_validate`. `located_in` has a partial unique constraint (one per
  source). `associated` is stored once, with the lower UUID as source. SQLite runs with
  `transaction_mode=IMMEDIATE`, so validate-then-write is serialised.
- **History** (`maintenance.ActivityEntry`) is append-only: corrections create a new entry
  with `correction_of` set and mark the old one `superseded`. It is separate from the
  `core.AuditEvent` admin log (`core.audit.record`). Documents are immutable
  `DocumentRevision`s. `ActivityDocument` pins a revision; `EntityDocument` and
  `TaskDocument` point at the document.
- **Recurrence.** Pure date maths is in `core/dates.py` (`add_interval` clamps from the
  anchor) and `maintenance/recurrence.py`.
  - Fixed schedules generate occurrence `n` = anchor + n×interval, up to a horizon, tracked
    by `next_index`. Tasks are unique on (schedule, occurrence_key).
  - After-completion schedules create the next task when the current one is completed or
    skipped.
  - Schedule edits delete and regenerate only *open* tasks with a due date on or after
    today.
- **Notifications** are an outbox (`NotificationDelivery`, unique on event_key + recipient +
  endpoint).
  - `notifications/services.py::plan_reminders` computes reminder times when they fall due,
    so membership and channel changes are honoured. Late reminders are folded into one
    catch-up summary per recipient and channel.
  - `dispatch` leases jobs with a conditional UPDATE, retries with exponential backoff, and
    redacts errors.
  - Channel adapters are in `notifications/adapters.py`, registered via
    `settings.NOTIFICATION_ADAPTERS`. In DEBUG/tests a `log` adapter is enabled.
- **Operations.**
  - `operations/archive.py` covers backup and verify: a SQLite backup API snapshot,
    sanitised (sessions, tokens and secrets removed), zipped with a checksummed manifest.
  - `operations/restore.py` swaps `DATA_DIR/instance` with a staged copy, using a journal
    and rollback copy.
  - `operations/maintenance_lock.py` is a lock file that blocks web writes (middleware) and
    scheduler jobs.
  - `operations/jobs.py` holds DB lease locks and heartbeats for the scheduler commands.
- **House manual** (`homefabric/manual/`):
  - `builder.py` lays the household out as numbered chapters: locations, then systems,
    policies, and other items. Each entity gets exactly one page; everywhere else it is a
    numbered `Ref` cross-reference.
  - The builder also produces the colour-coded SVG chart data.
  - `render.py` outputs the in-app view (stylesheet linked, so it stays CSP-safe), a single
    offline HTML file (CSS inlined, photos as data URIs), or a ZIP with relative links to
    bundled documents.
  - The print layout lives in `static/css/manual.css`. It is inlined into downloads, so it
    must not reference other files.
- **Data layout.** Under `DATA_DIR`: `instance/db.sqlite3`, `instance/blobs/` (opaque
  content-addressed files, per household), `staging/`, `secret_key`, `maintenance.lock`.
  Configuration is read in `homefabric/config.py` from `FABRIC_*` environment variables or
  a TOML file named by `FABRIC_CONFIG_FILE`.
- **Front end.** Templates are in `homefabric/templates/`, using a custom form renderer
  (`templates/partials/field.html`). Styling is a single `static/css/app.css`. The only
  JavaScript is the vendored htmx plus `static/js/app.js`. The strict CSP forbids inline
  scripts and styles: do not add `style=`, `<script>` blocks or CDN links. Every feature
  must keep working without JavaScript.

## Conventions

- New management commands subclass `core.cli.FabricCommand` (adds `--json`; `fail()` exits
  nonzero). Never accept secrets as command-line arguments.
- Money is stored as integer minor units plus a currency (`core/money.py`); never use
  floats. Date-only fields stay `DateField`.
- Must run natively on Windows, macOS and Linux. Use `pathlib`, `os.replace` and DB leases;
  do not use shell, symlinks, signals, `fcntl` or systemd-only features.
- Docs for operators and users are in `docs/`. Keep `docs/backup-format.md` in sync with
  `operations/archive.py` and `operations/export.py`.
