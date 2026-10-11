# Home Asset and Maintenance Manager

## Product and technical specification

**Version:** 1.0 — implementation proposal  
**Date:** 11 October 2026  
**Audience:** Product owner, engineering, design, and QA  
**Status:** Ready for technical review and estimation. Proposed defaults below are not additional commitments from the product owner.

## 1. Purpose

Build a self-hosted web application that maintains a durable record of a home, its rooms, systems, equipment, policies, documents, and maintenance. Users must be able to attach tasks, recurring schedules, documents, and dated notes to any entity and navigate both physical location and functional relationships.

The product should answer: What do we own? Where is it? What is it connected to? What needs doing? What happened previously? Where are the relevant documents?

The defining requirement is one record per real object, reachable through multiple relationships. A bedroom radiator belongs to the heating system and is located in the bedroom without being duplicated.

## 2. Confirmed needs and proposed defaults

| Confirmed need | Proposed implementation default |
| --- | --- |
| Web application and database | Responsive server-rendered Django application with HTMX |
| Hierarchical objects with tasks, documents, and notes | Typed entities plus explicit relationships |
| Building-wide insurance, mortgage, and renewal tracking | Policy/agreement entities with dates, attachments, and linked tasks |
| Equipment manuals and service histories | Entity documents and dated activity entries |
| Recurring maintenance | Schedule templates generating distinct task occurrences |
| Accounts including administration | Household roles plus a separate instance administrator |
| Modular notifications | Persistent outbox and provider adapters, initially email and Apprise |
| Backup export and restore | Full-instance archive and CLI restore |
| Unix and Windows portability | Native Python installation; optional Docker distribution |
| CLI configuration acceptable | Environment configuration and documented management commands |

Initial deployment is one self-hosted instance for a household. The data model supports multiple properties within a household. Multiple isolated households on one instance are supported by authorization boundaries, but commercial SaaS billing, tenant provisioning, and enterprise operations are outside the first release.

## 3. Scope and priorities

### Release 1 — required

- Entity creation, editing, archiving, relationships, tags, and structured attributes.
- Browsing by location and system, with entity detail pages and search.
- Private document uploads, downloads, metadata, and revisions.
- Dated notes, service records, costs, and task completion history.
- One-off tasks, recurring schedules, assignment, due dates, and reminders.
- Accounts, household membership, roles, and instance administration.
- Persistent notifications with retries, delivery records, and test delivery.
- Full backup, verification, restore, and readable data export.
- Documented native installation, scheduled execution, upgrades, and recovery.

### Later candidates

Calendar feeds, QR labels, OCR/PDF full-text search, public API, mobile offline support, contractor access, inventory import tools, richer financial reporting, and external home-automation integrations.

### Explicitly outside Release 1

Bank connections, mortgage calculations or financial advice, automatic policy purchasing/renewal, payment processing, native mobile apps, automated equipment control, AI-generated maintenance recommendations, and a general workflow/plugin marketplace.

## 4. Core user journeys

### 4.1 Document the home

An owner creates a property, utility room, bedroom, central-heating system, boiler, MagnaClean Professional 2, and radiator. They link equipment to both its location and system, attach manuals, and add installation dates and model numbers. Opening the boiler exposes associated equipment without merging their individual histories.

### 4.2 Maintain the boiler

A user creates an annual service schedule and a separate booking task in advance. Completing the booking task records only that booking; it does not mark the service complete. Completing the service occurrence records the actual date, provider, notes, cost, and invoice, then calculates the next due date according to the chosen recurrence rule.

### 4.3 Track building-level agreements

A user creates an insurance policy linked to the property, uploads the policy, records its expiry/renewal date, and configures a review reminder. A mortgage agreement can similarly have documents and a fixed-rate review date. The product records dates and reminders without asserting that an agreement renewed automatically.

### 4.4 Diagnose a problem

A user finds a radiator by name, room, or heating-system membership and sees its manual, latest work, unresolved tasks, and related equipment. They add a dated observation and create a repair task from it.

### 4.5 Move to another host

An instance administrator exports a backup, verifies it, installs a compatible release on another supported operating system, restores the archive, configures secrets, and checks documents, logins, relationships, tasks, and notification settings before resuming delivery.

## 5. Entity and relationship rules

Every entity has a stable UUID, household, name, type, description, status, tags, timestamps, and creator/editor. Equipment can include manufacturer, model, serial number, purchase/installation dates, warranty end date, and optional cost/currency. Names need not be unique.

Seed types: property, floor, room, system, equipment, component, policy, and agreement. Owners may add type labels. Do not require a new database table for each appliance type. Store query-critical fields in typed columns; use validated JSON attributes for less common details. Arbitrary JSON must not replace all relational modelling.

| Relationship | Meaning and constraints |
| --- | --- |
| Located in | One immediate location per entity; location containment forms an acyclic hierarchy |
| Part of | Functional membership/component relationship; multiple parents allowed; directed cycles forbidden |
| Associated with | Symmetric cross-reference; no self-links or duplicate inverse links |
| Covers / applies to | Policy or agreement applies to a property or asset; direction is explicit |

All relationships must remain within one household. Validate cycle rules on writes, including concurrent changes. A property is a top-level location. Support at least ten levels of nesting in the interface without assuming a fixed building/floor/room depth.

Archiving preserves history and makes an entity read-only until restored. Before archiving an entity with active schedules or open tasks, require a clear choice to retain or cancel that outstanding work; never silently drop it. Do not automatically archive related entities. Hard deletion is excluded from normal Release 1 workflows.

## 6. Logical data model

| Model | Key fields and responsibilities |
| --- | --- |
| User | Custom Django user model established before initial migrations |
| Household / Membership | Name, timezone, currency; user role and active membership |
| Entity / EntityType | Common fields, typed attributes, lifecycle status |
| EntityRelationship | Source, target, relationship kind, optional label |
| Document / DocumentRevision | Household, display name, MIME type, storage key, size, checksum, revision, uploader |
| EntityDocument / ActivityDocument / TaskDocument | Explicit attachment tables with foreign-key integrity |
| ActivityEntry | Primary entity, event type, occurrence date/time, author, notes, provider, optional amount/currency |
| Task | Entity, optional schedule, occurrence key, title, assignee, status, due date, completion time and result |
| MaintenanceSchedule | Entity, recurrence mode, interval, anchor date, local timezone, generation cursor, enabled state |
| ReminderRule | Task/schedule reference, lead time, recipients, channels |
| NotificationEndpoint | User, provider, display name, secret reference, enabled state |
| NotificationDelivery | Event key, recipient, channel, due time, state, lease, attempts, next retry, redacted error |
| AuditEvent | Actor, action, object reference, timestamp, changed fields and relevant request identifier |

All household-owned records require household scoping. Enforce same-household foreign references in the service layer and database where practical. Use integer minor units or fixed-precision decimals for money; never floating point. Preserve currency per recorded cost; do not sum unlike currencies without a conversion feature.

Service history is distinct from the administrative audit log. Corrections must preserve original values and author/timestamp. Documents are immutable revisions; replacing a manual does not replace an invoice attached to an older service record. Optimistic concurrency checks must prevent silent overwrites when two users edit the same record.

## 7. Tasks, recurrence, and date semantics

### Task lifecycle

Statuses: open, in progress, completed, skipped, cancelled. Overdue is derived from the due date and status. Completed work creates a linked activity entry. Skipping requires a reason and creates a history event, but never a service-completion record. Assignment is optional; inactive users cannot receive new assignments.

Use due dates for ordinary household maintenance. Store timestamps in UTC and display them in the household timezone. Date-only values must remain calendar dates when timezone settings change. Initial locale defaults are English (UK), Europe/London, and GBP; all are configurable.

### Recurrence modes

1. **Fixed calendar:** Every N days/weeks/months/years from an anchor. Completing late does not move the schedule. A 31st-of-month anchor clamps to shorter month ends and returns to the 31st subsequently. A 29 February annual anchor uses 28 February in non-leap years.
2. **After completion:** Next due date is the actual completion date plus the interval. Only one unfinished occurrence is generated. Skipping requires the user to supply a new due date or pause the schedule.

For fixed-calendar schedules, generate occurrences through a configurable horizon (default 90 days). Each occurrence has a unique schedule/date key. After downtime, generate missed occurrences as overdue, but send at most one catch-up summary per recipient/channel per run. Provide a bulk skip action with a reason. Setting a different future due date for one occurrence must not change the schedule anchor.

For completion-based schedules, accept backdated completion dates; calculate from that actual date and show the result even if the next task is already overdue. Do not create fictional completions to catch up.

Schedule edits affect unstarted future occurrences only. Preserve completed, skipped, cancelled, and in-progress occurrences; show which future tasks will change. Disabling a schedule stops generation and future reminders for its generated tasks, while preserving records. Re-enabling shows a catch-up preview. Reopening a completed recurring task is deferred; Release 1 offers an audited correction or follow-up task instead.

### Booking versus service

A booking task may reference a service task via a related-task link. Completing it has no automatic effect on service status. Release 1 may create this booking task manually; automated booking templates are optional.

## 8. Notification behaviour

Application code creates delivery jobs; channel adapters perform delivery. Initial channels are SMTP email and an Apprise adapter, with an interface allowing additional providers without changing recurrence logic. Notification content includes item name, task, due date, and an authenticated application link; never attach private documents automatically.

Default reminder: seven days before due date at 09:00 household time. Users can add reminders or disable them. Do not repeatedly notify overdue tasks by default. Delivery occurs on the first scheduler tick at or after the target time. Across daylight-saving changes, an ambiguous local time fires once; a nonexistent time moves to the next valid local time.

Use a unique event/recipient/channel key to prevent duplicate job creation. Workers claim jobs using an atomic lease; retries use bounded backoff and a configurable maximum (default five attempts). Record terminal failures and display them to the relevant user/owner. Providers may produce duplicate deliveries after an ambiguous timeout; do not claim exactly-once external delivery.

The scheduler must catch up after downtime, honour membership/channel changes, and suppress pending reminders for completed or cancelled tasks. The dashboard shows last successful scheduler run and warns when stale. A manual test-send action verifies each endpoint. Provider credentials and sensitive URLs are masked in UI and logs.

## 9. Accounts and permissions

No public registration by default. Bootstrap the first instance administrator by CLI. Owners invite household members using expiring single-use links; provide a local setup-link workflow if email is unavailable. Include login, logout, password change/reset, user deactivation, and session invalidation. Never remove or deactivate the last household owner without appointing a replacement.

| Capability | Viewer | Editor | Household owner | Instance administrator |
| --- | --- | --- | --- | --- |
| Read household entities/history/documents | Yes | Yes | Yes | Yes, across instance |
| Create/edit records and complete tasks | No | Yes | Yes | Yes |
| Archive entities and configure schedules | No | Yes | Yes | Yes |
| Manage household members/settings | No | No | Yes | Yes |
| Export household-readable data | No | No | Yes | Yes |
| Full-instance backup/restore and server configuration | No | No | No | Yes, CLI restore |

Viewer access includes document downloads. Release 1 has no per-document or per-entity visibility rules; disclose this in membership UI. An instance administrator is a trusted operator with access to all instance data. Household ownership must not imply instance administration.

Protect every query, mutation, search result, attachment, and export with authorization checks. A UUID or file URL is not authorization. Files must not be served from an unauthenticated public uploads directory.

## 10. Web application experience

Required screens:

1. Dashboard: due soon, overdue, assigned tasks, recent work, and notification health.
2. Property explorer: location tree and system view using the same records.
3. Entity detail: overview, relationships, tasks, documents, and chronological history.
4. Task list/detail: filters, assignment, completion with evidence, and recurrence preview.
5. Documents: metadata search, revisions, entity links, and authenticated downloads.
6. Search: names, descriptions, tags, model/serial, note text, document names; PDF content indexing is later scope.
7. Household settings: members, timezone, currency, and notification preferences.
8. Administration: operational status and backup/export guidance.

Responsive layouts must work at 360 px mobile width and on desktop. Core operations must be keyboard accessible with labelled fields, visible focus, meaningful errors, and status conveyed beyond colour. Target WCAG 2.2 AA for core flows. Pagination is required; do not load the whole entity graph at once.

## 11. Documents and storage

Allow PDF, common image formats, plain text, and Office documents as downloads. Default maximum file size: 50 MiB, configurable. Validate file size and content type, reject executable/active web formats, use opaque storage names, and prevent path traversal. Do not execute or process Office macros. Render untrusted notes as escaped plain text or sanitised Markdown with raw HTML disabled.

Store uploads in a managed local data directory and retain original display filenames as metadata. Record SHA-256 checksums. Identical content may be deduplicated internally without exposing files across households. File writes and database metadata must have a recoverable staging workflow so failed uploads do not appear as valid documents. Provide an integrity command to report missing files and orphaned data.

## 12. Proposed technical architecture

Use a modular Django monolith with modules for accounts, assets, documents, maintenance, notifications, and operations. Keep business rules in services shared by web views and CLI commands. Use Django templates plus HTMX for progressive enhancement and Django admin for trusted administration, not the primary household UI.

SQLite on local disk is the Release 1 default. Use short transactions, a documented busy timeout, and concurrency tests for web and scheduled work. No network-share database files. Design models and migrations to remain compatible with PostgreSQL, but do not advertise PostgreSQL support until integration, export/import, and restore tests pass.

Use Waitress or another validated cross-platform production server. Serve static assets through an appropriate production path; protect uploaded documents separately. A reverse proxy provides HTTPS when remotely accessed. Run the scheduler as a separate invocation/process, never inside each web worker. No mandatory Redis, Celery, Node.js runtime, or external hosted service in Release 1.

Pin a supported Python/Django combination during implementation and document dependency update policy. Package browser assets with releases so normal self-hosting does not depend on a CDN. External notification delivery may require internet access; browsing and record management must work offline on the local network.

## 13. Operations, CLI, and portability

The following are proposed command contracts; exact naming may change while semantics remain stable:

```text
manage.py bootstrap_admin
manage.py process_schedules --once
manage.py process_notifications --once
manage.py check_integrity
manage.py backup --output <archive>
manage.py verify_backup <archive>
manage.py restore <archive> --dry-run
manage.py restore <archive> --confirm
manage.py export_household <id> --output <archive>
```

Commands return nonzero status on failure and offer machine-readable JSON output where useful. Never accept passwords or provider secrets as required command-line arguments visible in process listings. Configure data path, bind address, public base URL, secret key, timezone defaults, and providers through environment variables or a restricted-permission configuration file.

Run both scheduler commands every minute using cron/systemd timers on Unix or Task Scheduler on Windows. Provide examples for each and prevent overlapping jobs. Docker is optional, with persistent data volume and documented backup instructions; it must not be the only Windows path.

Supported release targets: current supported Windows and macOS releases and a documented Linux LTS distribution, with x86-64 coverage and ARM64 where validated. Other Unix systems are best effort until tested. CI must test Linux, Windows, and macOS; no unconditional dependency on bash, symlinks, Unix signals, or systemd.

Provide readiness/liveness checks, structured logs, redaction, scheduler heartbeat, and database/file-integrity diagnostics. Release documentation must cover setup, account recovery, configuration, upgrades, backup automation, and disaster recovery. Take a backup before migrations; downgrades use a compatible prior backup rather than unsupported reverse migrations.

## 14. Backup, restore, and export

### Full-instance backup

Archive contains a consistent SQLite snapshot, every referenced uploaded file/revision, a versioned manifest, checksums, counts, and non-secret configuration. Include user/password-hash data and explain that the archive is sensitive. Secrets, active sessions, reset/invitation tokens, and external provider credentials are excluded or invalidated; list required secrets for recovery without their values. Use a documented standard encryption tool for automated off-host storage rather than inventing cryptography.

Coordinate a maintenance lock respected by web writes and workers; finish in-flight writes, pause generation/delivery, and use SQLite's backup API. Capture files and snapshot under the same write freeze. Check disk space before starting. Release the freeze reliably after success or failure. A failed backup must not overwrite the last valid archive.

### Restore

Full-instance replacement only in Release 1, performed by an instance administrator via CLI with explicit confirmation. No merge restore. Dry-run validates archive structure, checksum, size limits, schema compatibility, and available disk space without changing live data. Reject unsafe paths, symlinks, decompression bombs, corrupt archives, and newer unsupported schemas.

Stop web writes and workers, extract into a staging directory, validate records/files, and preserve the existing installation data as a rollback copy. Use a recoverable, platform-tested cutover process; do not assume replacing a directory is atomic on every OS. On failure retain or recover the pre-restore instance.

Restore with notifications paused and sessions/tokens invalidated. Previously delivered jobs remain delivered; unresolved jobs require a catch-up preview before sending. Mark any in-flight jobs for review. Require verification of the public URL and notification credentials before resumption. A restore must never immediately send a backlog of messages.

### Readable export

Owners can export only their household as versioned JSON plus CSV summaries and documents, preserving stable IDs and relationships. Exclude account credentials and provider secrets. This is a portable data extract, not a full operational backup. Cross-database import and archive merging are later scope.

## 15. Non-functional targets

Proposed acceptance envelope: 10 users, 3 concurrent active users, 10,000 entities, 100,000 history entries, and 10,000 document metadata records on a 2-vCPU/4-GB RAM host with SSD storage. File capacity is limited by configured disk space, not this metadata envelope.

- Dashboard/entity pages: p95 server response below 1 second under the stated test workload, excluding file transfer/network latency.
- Search: p95 below 2 seconds with pagination under the same workload.
- Notification jobs become eligible within 2 minutes of their configured time when the scheduler is healthy; provider delivery time is excluded.
- No cross-household access through UI, direct URLs, search, exports, or document download.
- CSRF protection, secure session configuration for HTTPS, login throttling, dependency checks, and authenticated private file access.
- Successful recovery of a representative backup on a different supported OS, including file checksums and user access.
- Proposed operational target: daily automated backups, 24-hour recovery-point objective, and documented recovery within one hour for a tested 5-GB instance on local SSD. These are deployment targets to benchmark, not guarantees without backup scheduling and retained copies.

## 16. Acceptance scenarios

| ID | Scenario and expected result |
| --- | --- |
| A01 | Create house, rooms, heating, boiler, MagnaClean, and radiator; show the same radiator record under bedroom and heating. |
| A02 | Attempt a containment cycle or cross-household relationship; reject without partial writes. |
| A03 | Attach a manual to boiler and a different manual to MagnaClean; each remains independently retrievable. |
| A04 | Complete a booking task; service occurrence remains open and no service record is fabricated. |
| A05 | Complete annual service with date, invoice, provider, notes, and cost; preserve history and calculate next due date correctly for each recurrence mode. |
| A06 | Test 31 January monthly recurrence, leap-day annual recurrence, backdated completion, skipped tasks, and UK DST transitions against the stated rules. |
| A07 | Run schedulers concurrently and after downtime; no duplicate occurrences/jobs, catch-up summary behaves as specified, and leases recover after a killed worker. |
| A08 | Simulate provider outage then recovery; bounded retries, visible failures, and credential redaction all work. |
| A09 | Viewer attempts mutation; unrelated household user attempts direct file download/search/export; all are rejected. |
| A10 | Replace a document revision and correct a history entry; prior versions and authors remain traceable. |
| A11 | Archive an asset with outstanding work; selected cancellation/retention behaviour is explicit and history remains readable. |
| A12 | Export a live instance during attempted concurrent writes; restore to another supported OS with matching IDs, relationships, counts, and document checksums. |
| A13 | Restore a corrupt, malicious, or unsupported-version archive; fail before replacing live data. Simulated cutover failure permits recovery. |
| A14 | Restore a valid archive containing overdue jobs; notifications remain paused until the administrator reviews and resumes them. |
| A15 | Deactivate a member; their session and household access cease while authored history is preserved. |
| A16 | Complete all core flows by keyboard and at 360 px width; meet accessibility and performance targets. |

## 17. Delivery plan and definition of done

1. **Design validation:** Approve schema, wireframes, recurrence examples, permission matrix, and restore threat/failure cases. Demonstrate the boiler scenario using fixtures.
2. **Foundation:** Accounts, household isolation, entities, relationships, documents, and explorer. Exit when A01–A03 and authorization checks pass.
3. **Maintenance:** Tasks, history, schedules, outbox, adapters, and dashboard. Exit when A04–A11 and recurrence/concurrency tests pass.
4. **Operations and release:** Backup/restore/export, cross-platform installation, recovery drills, accessibility/performance checks, and documentation. Exit when remaining acceptance scenarios pass.

The team should estimate these increments after design validation. No fixed timeline is implied. Deliver source code, migrations, dependency lockfiles, automated tests, test fixtures, installable release/container, operator guide, backup format documentation, and a concise user guide. Test business invariants, permission boundaries, failure recovery, and portability; avoid tests that merely mirror implementation.

## 18. Decisions for technical review

The team can proceed using the proposed defaults, but must record decisions on: supported OS/CPU versions; selected Python/Django versions; whether PostgreSQL is included in the first release; file-size/storage limits; backup encryption tooling; identity recovery without email; and whether multiple households need full UI exposure initially.

Product owner review is needed before expanding scope to paid SaaS, contractors with restricted document access, native mobile apps, bank integrations, or an extensible third-party plugin runtime. These materially change the security model and operating burden.

## 19. Implementation references

These are engineering references, not substitutes for the requirements above. Verify supported versions during implementation.

- Django authentication: https://docs.djangoproject.com/en/stable/topics/auth/
- Django administration: https://docs.djangoproject.com/en/stable/ref/contrib/admin/
- Django management commands: https://docs.djangoproject.com/en/stable/howto/custom-management-commands/
- Django deployment checklist: https://docs.djangoproject.com/en/stable/howto/deployment/checklist/
- HTMX documentation: https://htmx.org/docs/
- Apprise: https://github.com/caronc/apprise
- SQLite backup API: https://www.sqlite.org/backup.html
- Waitress: https://docs.pylonsproject.org/projects/waitress/en/stable/
