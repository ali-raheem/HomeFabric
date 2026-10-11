# Changelog

All notable changes are recorded here. HomeFabric follows
[Semantic Versioning](https://semver.org/):

- **MAJOR**: incompatible changes. Examples: a backup or export format older releases can't
  read, removed commands or configuration keys, or behaviour that existing data or scripts
  rely on.
- **MINOR**: backwards-compatible features, including new database migrations.
- **PATCH**: backwards-compatible bug fixes only.

The version lives in `homefabric/__init__.py`.

**Restore compatibility.** An instance restores backups from its own release and from older
releases (they are migrated forward). It rejects backups made by a release with a newer
database schema. Upgrade first, then restore.

## [1.2.1] - 2026-10-11

### Fixed

- Restoring a backup on Windows failed with "Access is denied" while moving the document
  store into place. The previous instance was kept intact, but the restore could not finish.
- Downloaded house manuals (single HTML file) no longer get Windows line endings when
  generated on Windows.

## [1.2.0] - 2026-10-11

### Added
- **Archiving and permanent deletion of households** for instance administrators, via
  Administration → Households and `manage.py household list|archive|restore|delete`.
  - Archiving is reversible. It hides the household from its members, stops schedule
    generation and reminders (queued reminders are cancelled), and makes its invitations
    unusable.
  - Permanent deletion is only allowed for archived households and needs the exact
    household name. It removes the household's items, relationships, history, tasks,
    schedules, documents and stored files, memberships and invitations.
  - User accounts are kept. You can optionally deactivate accounts that belong only to
    the deleted household.
  - The deletion runs in one transaction. Files are removed only after it commits, and an
    audit entry records what was deleted.
- An administrator can export any household's data from the Households page, including
  archived ones.

### Upgrade notes
- Adds migration `accounts.0002_household_archive`. Run `homefabric upgrade`.
- Backups made by 1.2.0 cannot be restored on earlier releases.

## [1.1.0] - 2026-10-11

### Added
- **House manual**: a printable handbook for people who don't use HomeFabric. It has:
  - numbered contents organised by room, then systems, policies and unplaced items;
  - an "at a glance" section with upcoming maintenance, recurring schedules and policy
    renewals;
  - colour-coded charts of where things are and of how systems fit together;
  - a page per item covering location, systems, details, photos, documents, maintenance and
    history.

  Each item is printed once and cross-referenced by number wherever else it appears.
  Available as an in-app view (print or save as PDF), a single offline HTML file, a ZIP
  bundling the referenced documents (owners only), and `manage.py house_manual`.
- **Sort position** for items. It orders siblings, such as floors within a property, in
  Explore, on item pages and in the house manual. Unpositioned items keep the previous
  order: by kind, then by name.
- **Part of** multi-select on the item add/edit form, so an item can be placed in a room
  and made part of several systems in one save.
- `sort_order` is included in household exports.

### Changed
- Item create and edit now save details, location and system memberships in one
  transaction. A rejected change, such as a membership that would form a loop, leaves the
  item unchanged.
- "Add a new component" on a system pre-selects that system in the item form.

### Fixed
- The systems chart in the house manual now shows an item under every system it belongs
  to.

### Upgrade notes
- Adds migration `assets.0003_entity_sort_order`. Run `homefabric upgrade`, which backs up
  first.
- Backups made by 1.1.0 cannot be restored on 1.0.0 because the schema is newer.

## [1.0.0] - 2026-10-11

Initial release implementing `Product-Specification.md` release 1:
- items and relationships;
- private versioned documents;
- service history;
- tasks and recurring schedules;
- notifications;
- accounts and roles;
- backup, restore and export;
- deployment files for Linux, macOS, Windows and Docker.
