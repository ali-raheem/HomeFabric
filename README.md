# HomeFabric

A self-hosted web application for keeping a durable record of a home: what you own, where it
is, what it is connected to, what needs doing, what happened before, and where the documents
are.

Each real object has exactly one record, and you can reach it through more than one
relationship. A bedroom radiator is located in the bedroom and is part of the heating system.
It is never duplicated.

## Features

- **Items and relationships**: properties, floors, rooms, systems, equipment, components,
  policies and agreements, plus your own type labels. Each item has one location (no
  location loops), can be part of several systems (no loops), can be associated with other
  items, and policies/agreements can cover items. Items have tags, typed details and free-form
  attributes.
- **Explorer**: browse by location or by system. Both views show the same records, and
  children load as you expand them.
- **Documents**: private uploads with immutable revisions and SHA-256 checksums. Files are
  checked by content, and executable or active formats are rejected. Downloads require
  sign-in.
- **History**: dated notes, observations, service records, costs (exact decimal amounts, one
  currency per entry) and attachments. Entries are never edited in place: a correction keeps
  the original and its author.
- **Tasks and schedules**:
  - one-off and recurring tasks;
  - *fixed calendar* and *after completion* recurrence, with month-end and 29 February
    clamping;
  - booking tasks that never complete the service they book;
  - skip with a reason, bulk skip, a catch-up preview after re-enabling a schedule, and edit
    previews.
- **Notifications**:
  - email (SMTP) and [Apprise](https://github.com/caronc/apprise) channels;
  - a persistent outbox with leases, bounded retries and visible failures;
  - DST-correct reminder times;
  - one catch-up summary per channel after downtime;
  - credential masking.
- **Accounts**:
  - no public registration;
  - single-use, expiring invitations;
  - viewer, editor and owner roles, plus a separate instance administrator;
  - session invalidation, login throttling, and a rule that a household always keeps an
    owner.
- **House manual**: a printable handbook for someone who doesn't use HomeFabric. It has
  numbered contents by room and system, a colour-coded map of the home, and a page per item
  covering details, photos, documents, maintenance and history. View it and print to PDF,
  download it as one offline HTML file, or download a ZIP that includes the documents.
- **Operations**:
  - consistent full-instance backups, verification, and a restore that can be rolled back
    (CLI only);
  - per-household readable export;
  - integrity checks, health endpoints, structured logs and scheduler heartbeat.

Runs natively on Linux, macOS and Windows with Python 3.11+. It needs no Redis, Celery,
Node.js or CDN, and browsing works offline on your local network.

## Quick start (development)

```bash
uv sync                                   # or: python -m venv .venv && pip install -r requirements.txt
export FABRIC_DATA_DIR=./data FABRIC_DEBUG=1
uv run python manage.py migrate
uv run python manage.py bootstrap_admin --username you --household "Our home"
uv run python manage.py seed_demo --owner you        # optional: the boiler example
uv run python manage.py runserver
```

To run scheduled processing while developing:

```bash
uv run python manage.py run_scheduler
```

## Production

See **[docs/operator-guide.md](docs/operator-guide.md)** for installation on each OS, configuration,
HTTPS, scheduled jobs, backups, upgrades and disaster recovery. Ready-made service files live
in [`deploy/`](deploy/).

## Documentation

| Document | For |
| --- | --- |
| [docs/user-guide.md](docs/user-guide.md) | Household members |
| [docs/operator-guide.md](docs/operator-guide.md) | Whoever installs and runs the instance |
| [docs/backup-format.md](docs/backup-format.md) | Backup and export archive formats |
| [docs/decisions.md](docs/decisions.md) | Recorded technical decisions (spec §18) |
| [docs/accessibility.md](docs/accessibility.md) | Accessibility approach and manual test checklist |
| [Product-Specification.md](Product-Specification.md) | The product specification |

## Tests

```bash
uv run pytest -m "not slow"     # full functional suite, acceptance scenarios A01–A16
uv run pytest -m slow -s        # performance envelope: 10k items, 100k history entries
uv run ruff check .
```
