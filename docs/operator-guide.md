# HomeFabric operator guide

This guide is for the **instance administrator**: the trusted person who installs, configures,
backs up and restores HomeFabric. The administrator can read all data on the instance.

## 1. Requirements

| | Supported |
| --- | --- |
| Python | 3.11, 3.12, 3.13 (CI-tested; 3.12 recommended) |
| Django | 5.2 LTS (pinned in `uv.lock` / `requirements.txt`) |
| OS | Current Windows 10/11 and Server releases, current macOS, Ubuntu 24.04 LTS. The CI matrix covers x86-64 on all three and ARM64 on Linux and macOS. Other Unix systems are best effort. |
| Database | SQLite on a **local** disk. Never put the data directory on a network share. |
| Hardware | 2 vCPU / 4 GB RAM / SSD handles 10 users and 10,000 items. File storage is limited only by disk space. |

## 2. Install

### Linux (systemd)

```bash
sudo useradd --system --home /var/lib/homefabric homefabric
sudo mkdir -p /opt/homefabric /var/lib/homefabric /etc/homefabric /var/backups/homefabric
sudo chown homefabric: /var/lib/homefabric /var/backups/homefabric
cd /opt/homefabric
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt     # or: pip install homefabric-1.0.0-py3-none-any.whl
sudo cp deploy/fabric.example.toml /etc/homefabric/fabric.toml
sudo chown root:homefabric /etc/homefabric/fabric.toml && sudo chmod 640 /etc/homefabric/fabric.toml
sudo -u homefabric FABRIC_CONFIG_FILE=/etc/homefabric/fabric.toml .venv/bin/homefabric migrate
sudo -u homefabric FABRIC_CONFIG_FILE=/etc/homefabric/fabric.toml .venv/bin/homefabric bootstrap_admin --username admin --household "Our home"
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now homefabric-web homefabric-schedules.timer homefabric-notifications.timer homefabric-backup.timer
```

If you prefer cron over systemd timers, use `deploy/cron/crontab.example`.

### macOS (launchd)

Install as on Linux under `/opt/homefabric`. Put the configuration in
`/usr/local/etc/homefabric/fabric.toml`, copy `deploy/macos/*.plist` to `~/Library/LaunchAgents/`
(to run as a user) or `/Library/LaunchDaemons/` (system-wide), then
`launchctl load <plist>`.

### Windows

1. Install Python 3.12 from python.org and tick "Add to PATH".
2. In PowerShell:
   ```powershell
   mkdir C:\HomeFabric; cd C:\HomeFabric
   py -3.12 -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   copy deploy\fabric.example.toml C:\HomeFabric\fabric.toml   # set data_dir = "C:/HomeFabric/data"
   $env:FABRIC_CONFIG_FILE = "C:\HomeFabric\fabric.toml"
   .venv\Scripts\homefabric migrate
   .venv\Scripts\homefabric bootstrap_admin --username admin --household "Our home"
   ```
3. Restrict the config file so only the service account and Administrators can read it:
   right-click → Properties → Security, and remove inherited permissions.
4. From an elevated PowerShell, run
   `deploy\windows\install-tasks.ps1 -InstallDir C:\HomeFabric -ConfigFile C:\HomeFabric\fabric.toml -BackupDir D:\Backups\HomeFabric`.
   This registers the web server (at startup), both scheduler jobs (every minute) and a
   nightly backup.

### Docker (optional)

```bash
cp deploy/fabric.example.toml .   # or create a .env with FABRIC_* variables
docker compose up -d
docker compose exec web homefabric bootstrap_admin --username admin --household "Our home"
```

All state lives in the `homefabric-data` volume. Back it up with
`docker compose exec web homefabric backup --output /data/backups/`, then copy the archive off
the host. Docker is not required on any OS.

## 3. Configuration

Configuration comes from environment variables `FABRIC_<KEY>` or from a TOML file named by
`FABRIC_CONFIG_FILE`; environment variables win. Any key can also be read from a file with
`FABRIC_<KEY>_FILE=/path`, which suits Docker or systemd secrets.

| Key | Default | Meaning |
| --- | --- | --- |
| `DATA_DIR` | OS app-data dir | Database, documents, staging and secret key |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Used in notification links and CSRF trusted origins |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Host names the server answers to |
| `BIND` | `127.0.0.1:8000` | Address for `homefabric serve` |
| `SECRET_KEY` | generated into `DATA_DIR/secret_key` | Django secret key |
| `BEHIND_HTTPS_PROXY`, `TRUSTED_PROXY` | off | Trust `X-Forwarded-Proto` from your reverse proxy |
| `SECURE_COOKIES` | on when public URL is https | Secure session/CSRF cookies |
| `HSTS_SECONDS` | 0 | Enable HSTS once HTTPS is confirmed working |
| `DEFAULT_TIMEZONE`, `DEFAULT_CURRENCY`, `LANGUAGE` | Europe/London, GBP, en-gb | Defaults for new households |
| `MAX_UPLOAD_MB` | 50 | Upload limit; also set your proxy's body limit |
| `SCHEDULE_HORIZON_DAYS` | 90 | How far ahead fixed schedules generate |
| `NOTIFY_MAX_ATTEMPTS` | 5 | Delivery attempts before a job is marked failed |
| `NOTIFY_BACKOFF_BASE_SECONDS` / `_MAX_SECONDS` | 60 / 3600 | Exponential retry backoff |
| `NOTIFY_CATCHUP_GRACE_SECONDS` | 900 | Reminders later than this are grouped into a catch-up summary |
| `SCHEDULER_STALE_SECONDS` | 600 | When the dashboard warns that jobs are not running |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_FROM` | — | SMTP for reminders, invitations and password resets |
| `SQLITE_BUSY_TIMEOUT` | 20 | Seconds a writer waits for the SQLite lock |
| `LOG_FORMAT` | `text` | `json` for structured logs (includes the request ID) |
| `LOG_LEVEL` | INFO | |
| `LOGIN_FAILURE_LIMIT`, `LOGIN_FAILURE_WINDOW` | 5, 900 | Login throttling |
| `DEBUG` | off | Never enable in production |

Run `homefabric check --deploy` after configuring.

### HTTPS

Run HomeFabric behind a reverse proxy that terminates TLS: `deploy/proxy/Caddyfile` (automatic
certificates) or `deploy/proxy/nginx.conf`. Set `PUBLIC_BASE_URL=https://…`,
`BEHIND_HTTPS_PROXY=true` and `TRUSTED_PROXY=127.0.0.1`. Static assets are served by the
application itself (WhiteNoise). Documents are only ever served through authenticated views,
never from a public directory.

## 4. Scheduled jobs

Both jobs must run **every minute**:

| Command | Purpose |
| --- | --- |
| `homefabric process_schedules --once` | Generates recurring task occurrences up to the horizon |
| `homefabric process_notifications --once` | Plans due reminders and delivers the outbox with retries |

A run that overlaps a previous one exits immediately: a database lease prevents concurrent
runs on every OS. While a backup or restore holds the maintenance lock, both jobs skip.
The dashboard and Administration page show the time of each job's last successful run, and
warn when that is more than 10 minutes ago.

For containers, `homefabric run_scheduler` runs both jobs in a loop.

## 5. Health and diagnostics

- `GET /healthz`: liveness (process is up).
- `GET /readyz`: readiness (database reachable, migrations applied, data directory
  writable). Returns 503 when not ready.
- `homefabric healthcheck --json`: the readiness checks plus scheduler freshness. Exits 1 when
  unhealthy, which suits monitoring.
- `homefabric check_integrity [--verify-checksums] [--clean] [--json]`:
  - SQLite integrity and foreign keys;
  - files that are missing, the wrong size or fail their checksum;
  - orphaned files and blobs;
  - stale upload staging files;
  - references that cross households.
- Every response carries an `X-Request-ID` header; logs and audit events record the same ID.
- Credentials and secret URLs are masked in the UI and redacted from logs.

## 6. Notifications

- **Email**: set the SMTP keys. Each user adds their own address under *Notifications*.
- **Apprise**: each user adds an Apprise URL (Telegram, Signal, ntfy, Slack, Discord, Matrix
  and more). URLs are stored privately, shown masked, and **never included in backups**.
  Advanced: a user can instead give the name of a server environment variable that holds the
  URL.
- *Send test* verifies a channel immediately, even while delivery is paused.
- Delivery is *at least once*. If a provider times out after actually accepting a message,
  the retry can produce a duplicate. Exactly-once external delivery is not possible and is
  not claimed.
- To pause or resume delivery, use Administration or
  `homefabric notifications pause|resume|status`.

## 7. Backups

```bash
homefabric backup --output /var/backups/homefabric/        # timestamped archive in that directory
homefabric verify_backup /var/backups/homefabric/homefabric-20261011-023000.zip
```

- **Consistency.** The backup takes the maintenance lock, which pauses web writes (users see
  "maintenance in progress") and scheduler jobs. It waits briefly for in-flight requests,
  snapshots the database with SQLite's backup API, and copies every referenced document
  while the lock is still held. The lock is always released, even on failure.
- **Safety.** It checks free disk space first and writes to a temporary file. The archive is
  verified before it replaces anything, so a failed backup never overwrites the previous
  good one.
- **What is included.** The database (with all user password hashes), all document
  revisions, checksums, counts, and non-secret configuration.
- **What is excluded or invalidated.** The secret key, sessions, invitation and reset tokens,
  and notification provider credentials. The manifest lists the secrets you must re-supply.

**The archive is sensitive.** Encrypt it before it leaves the host, using a standard tool:

```bash
age -r age1yourpublickey... -o backup.zip.age backup.zip      # https://age-encryption.org
gpg --encrypt --recipient you@example.org backup.zip
7z a -p -mhe=on backup.7z backup.zip                            # Windows-friendly AES-256
```

**Recommended schedule:**

- Nightly backup (`homefabric-backup.timer`, the cron example, or the Windows task).
- Encrypt and copy archives off the host.
- Keep at least 14 daily archives.
- Test a restore every quarter.

This gives a 24-hour recovery point. Recovering a 5 GB instance from local SSD takes well
under an hour.

## 8. Restore and disaster recovery

A restore replaces the **entire** instance; it never merges. To move to a new host, or to
recover:

1. Install the same release or a newer one on the target machine (any supported OS) and run
   `homefabric migrate`.
2. Decrypt the archive, then validate it without changing anything:
   ```bash
   homefabric restore archive.zip --dry-run
   ```
   This checks:
   - structure, checksums and the manifest;
   - unsafe paths, symlinks and decompression bombs;
   - the database's integrity;
   - schema compatibility (archives from newer releases are rejected);
   - free disk space.
3. Stop the web server and both scheduler jobs.
4. Run the restore:
   ```bash
   homefabric restore archive.zip --confirm
   ```
   It extracts and re-verifies the archive in a staging directory, then moves the current
   data aside to `DATA_DIR/rollback-<timestamp>/` and puts the new data in place. Each step
   is recorded in a journal. If anything fails, the previous data is put back automatically.
   If the process is killed part-way, run `homefabric restore --recover`.
5. Re-supply secrets: `FABRIC_SECRET_KEY` (or let a new one be generated) and the SMTP
   password. Users must re-enter Apprise URLs; their channels show "Needs credentials".
6. Start the web server and scheduler. Everyone must sign in again.
7. Check documents, logins, relationships, tasks and channels. Then, on **Administration →
   Notification delivery**:
   - confirm the public URL;
   - choose whether to send or discard notifications that queued before the backup;
   - resume delivery.

   A restore never sends a backlog on its own. Jobs that were in flight when the backup was
   taken are marked for review and are not resent automatically.
8. Delete `DATA_DIR/rollback-*` once you are satisfied.

**Downgrades:** reverse migrations are not supported. To go back to an older release,
restore a backup taken by that release, or by a release with a compatible schema.

## 9. Upgrades

1. Read the release notes.
2. Install the new release into the virtual environment (`pip install -r requirements.txt` or
   the new wheel).
3. Stop the web server.
4. Run `homefabric upgrade`. It takes a verified backup into `DATA_DIR/backups/` first, then
   applies migrations, and does nothing if the database is already current.
5. Start the web server.

**Dependency policy:** Django is pinned to the 5.2 LTS series, and all dependencies are
locked in `uv.lock`. CI audits dependencies weekly. Security releases are applied promptly
as patch upgrades, and non-security updates are applied with each release.

## 10. Accounts and recovery

- **First administrator.** Use `homefabric bootstrap_admin --username NAME`. The password is
  prompted for, or read with `--password-stdin`, and is never passed as an argument.
- **More households.** Use Administration → New household, or
  `homefabric create_household NAME --owner USER`.
- **Removing a household.** Use Administration → Households, or the CLI:
  ```bash
  homefabric household list
  homefabric household archive "Old flat" --reason "Sold"
  homefabric household restore "Old flat"
  homefabric household delete "Old flat" --confirm-name "Old flat" [--deactivate-sole-members]
  ```
  - **Archiving** is reversible. Members lose access, and reminders and schedule generation
    stop, but all the data is kept.
  - **Deleting** works only on an archived household, needs its exact name, and cannot be
    undone. Export the household first; the Households page has an Export button.
  - User accounts survive deletion, because people may belong to other households.
  - Instance backups taken before the deletion still contain the household until you
    rotate them out.
- **Forgotten password.** With email configured, users can reset their own password. Without
  email:
  - run `homefabric issue_setup_link USERNAME` and give the user the printed single-use link
    (valid for 24 hours); or
  - use Administration → Users → Password link.
- **Last administrator locked out.** Run `homefabric changepassword USERNAME` on the server.
- **Deactivating someone.** Owners can remove a member's access to their household. The
  instance administrator can deactivate an account everywhere. Both sign the person out
  immediately, and their history is kept.
- **Last owner.** A household always keeps at least one active owner.
- **Trusted administration.** Django's admin site is at `/django-admin/` for instance
  administrators only. History entries cannot be edited there.

## 11. House manual

Members can generate a printable manual from the web interface; see the user guide. Owners
can also download a ZIP of the manual together with its documents. From the command line:

```bash
homefabric house_manual "Our home" --output manual.html               # single offline file
homefabric house_manual "Our home" --output manual.zip --history all  # manual plus documents
```

The manual is generated on demand. A typical house of a few hundred items takes a second or
two. At the 10,000-item limit it takes about 30 seconds and produces around 16 MB of HTML,
so keep your reverse proxy's read timeout at 60 seconds or more (the provided nginx example
uses 300).

## 12. Security notes

- No public registration. Invitations are single-use, expire after 7 days, and only their
  hash is stored.
- Authorization is checked on every page, search, download and export. Knowing an ID or file
  URL grants nothing.
- Uploads:
  - content is checked by file signature;
  - executables, HTML, SVG, scripts and macro-enabled Office files are rejected;
  - files are stored under opaque names outside any web root;
  - downloads are served as attachments with `nosniff` and a sandboxing CSP.
- Notes are rendered as Markdown with raw HTML disabled, and only http, https and mailto
  links are allowed.
- Pages use a strict Content-Security-Policy (no inline script or style), CSRF protection,
  and HttpOnly SameSite cookies. Logins are throttled by username and by IP address.
