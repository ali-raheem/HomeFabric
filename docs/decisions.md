# Technical decisions (spec §18)

Recorded for release 1.0.0. Revisit any of these before expanding scope as described in
§18.

| Decision | Choice | Notes |
| --- | --- | --- |
| Python / Django | Python ≥ 3.11 (CI: 3.11–3.13); Django 5.2 LTS | 5.2 is supported until April 2028. Locked in `uv.lock`. |
| Supported OS / CPU | Windows 10/11 and Server, current macOS, Ubuntu 24.04 LTS; x86-64 everywhere, ARM64 on Linux and macOS | Defined by the CI matrix. Other Unix systems are best effort. |
| Database | SQLite only for 1.0 | Models avoid SQLite-only features and use row locks where PostgreSQL needs them, but PostgreSQL is **not** advertised. Backup and restore are SQLite-specific. |
| SQLite concurrency | WAL mode, `BEGIN IMMEDIATE` transactions, busy timeout 20 s | Serialises read-check-write validation (cycle checks, unique occurrences). |
| Web server | Waitress (`homefabric serve`), static files via WhiteNoise | Pure Python on every OS. |
| Front end | Server-rendered templates with htmx 2.0.4 included in the release | No CDN and no Node.js. Every feature also works without JavaScript. |
| File storage | Local `DATA_DIR/instance/blobs`, SHA-256, de-duplicated within one household | Default limit 50 MiB (`MAX_UPLOAD_MB`). Content is detected by signature. |
| Backup encryption | Not built in. Documented use of `age`, `gpg` or 7-Zip AES-256 | Avoids custom cryptography. |
| Identity recovery without email | Single-use password links from the CLI (`issue_setup_link`) or the Administration page; `changepassword` for the last administrator | |
| Multiple households | Fully enabled in the UI. Instance administrators create them; members switch between them in the header. | Isolation is enforced on every query. |
| Notification secrets | Stored in the database in a separate field, masked in UI and logs, stripped from backups. Can instead reference an environment variable (`secret_ref`). | |
| Reminder timing | Planned at fire time, not pre-created | Membership, channel and task changes are honoured automatically. |
| Catch-up behaviour | Reminders more than 15 minutes late, or tasks generated as already overdue, go into a single summary per recipient and channel for that run | Configurable grace period. |
| Hard deletion | Not available in normal workflows. Items are archived. Unwanted channels are soft-deleted. | |
