# Archive formats

## Full-instance backup (`homefabric-backup`, format version 1)

The backup is a ZIP file, using ZIP64 where needed:

| Entry | Content |
| --- | --- |
| `manifest.json` | Versioned manifest (below) |
| `manifest.sha256` | Hex SHA-256 of `manifest.json` |
| `db.sqlite3` | Consistent SQLite snapshot (online backup API), journal mode DELETE, sanitised |
| `blobs/<household-hex>/<xx>/<key-hex>` | Each stored file, byte-for-byte. Identical files within one household are stored once. |

The archive contains no other entries. Restore rejects:

- any other path, absolute paths, `..`, backslashes or duplicate names;
- symbolic links;
- entries whose size differs from the manifest;
- highly compressed entries larger than 10 MB (compression ratio above 200:1).

### `manifest.json`

```json
{
  "format": "homefabric-backup",
  "format_version": 1,
  "app_version": "1.0.0",
  "created_at": "2026-10-11T02:30:00+00:00",
  "source": {"platform": "...", "python": "3.12.3", "django": "5.2.x"},
  "schema": {"assets": ["0001_initial", "0002_seed_entity_types"], "...": []},
  "database": {"path": "db.sqlite3", "sha256": "...", "size": 123456},
  "files": [{"path": "blobs/…", "sha256": "...", "size": 1024}],
  "counts": {"assets_entity": 42, "...": 0},
  "config": {"public_base_url": "...", "default_timezone": "Europe/London", "...": "..."},
  "required_secrets": [{"name": "FABRIC_SECRET_KEY", "description": "..."}],
  "excluded": ["sessions", "invitation and reset tokens", "notification provider credentials", "secret key"],
  "warning": "This archive contains every household's data and user password hashes..."
}
```

### Sanitisation applied to the snapshot

- `django_session`, `accounts_loginattempt` and `operations_joblock` are emptied.
- Unused invitations get random token hashes and are marked revoked.
- Every notification endpoint's `secret_value` is blanked. Endpoints that had a secret or a
  secret reference are marked `needs_verification`.
- Every user's `session_epoch` is incremented, so copied session cookies are useless.
- The snapshot is `VACUUM`ed so the removed values do not remain in free pages.

### Compatibility rules

- `format_version` greater than this release supports → rejected.
- Any migration name in `schema` that this release does not know → rejected as a newer
  schema.
- Older schemas are accepted. They are migrated forward after cutover.

## Household export (`homefabric-household-export`, format version 1)

A ZIP file with:

- `README.txt`.
- `export.json`, containing:
  - `household` and `members` (usernames, display names, emails, roles; no credentials);
  - `entity_types`, `tags`, `entities`;
  - `relationships` (`source`/`target` entity IDs and `kind`: `located_in`, `part_of`,
    `associated`, `covers`);
  - `documents`, each with its `revisions` and a `path` into the ZIP;
  - `entity_documents`, `task_documents`;
  - `activity` (with `correction_of`, `superseded` and pinned `document_revisions`);
  - `schedules`, `tasks`, `reminder_rules`.
- `entities.csv`, `tasks.csv`, `activity.csv`, `documents.csv`: human-readable summaries.
- `documents/<document-id>/r<revision>-<filename>`: every revision. Its SHA-256 matches
  `export.json`.

All IDs are the application's stable UUIDs. Money is stored as integer minor units (for
example pence) together with an ISO 4217 currency code. Dates are ISO 8601. Timestamps are
in UTC.
