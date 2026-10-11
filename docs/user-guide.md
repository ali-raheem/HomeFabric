# HomeFabric user guide

HomeFabric keeps one record for each real thing in your home and lets you find it in more
than one way:

- by **where it is**: property, floor, room;
- by **what it belongs to**: for example, the central heating system.

## Roles

| You are a… | You can |
| --- | --- |
| Viewer | See everything in the household, including history, and download documents |
| Editor | Also add and change items, upload documents, complete tasks, manage schedules, archive items |
| Owner | Also invite people, change roles, change household settings, export the household's data |

Access is household-wide: there are no private items or documents inside a household.

## Getting started

1. Accept the invitation link you were sent and create your account.
2. Under **Notifications**, add an email address or an Apprise URL, then press **Send test**.
   Without a channel you will not get reminders.
3. Add your **property** (Items → Add item → type *Property*). Then add floors and rooms,
   choosing the property or floor as **Located in**.

## Items

- Pick a type. Owners can add their own types, such as "Boiler" (behaves as equipment), under
  Household → Item types.
- Fill in what you know:
  - equipment: manufacturer, model, serial number, dates, warranty, cost;
  - policies and agreements: provider, reference, start, expiry, and renewal or review date.
  Anything else goes in **Other details** as `name: value` lines.
- **Location.** Every item has one immediate location. Use **Move** to change it.
- **Sort position** (optional) sets the order among items in the same place. For example,
  give Ground floor 1 and First floor 2. The order is used in Explore, on item pages and in
  the house manual. Items without a position come after, grouped by kind and then sorted
  alphabetically.
- **Relationships** (on the item page):
  - *Part of*: the radiator is part of the central heating. An item can be part of several
    systems.
  - *Associated with*: a two-way link, such as a boiler and its MagnaClean filter.
  - *Covers*: a policy or agreement applies to a property or asset.

  HomeFabric refuses links that would create a loop, and links between households.
- **Explore** shows the same items as a location tree or a system tree. Press **+** to expand
  a branch.
- **Archive** an item you no longer have. Its history stays, but it becomes read-only until
  restored. If it still has open tasks or active schedules, you choose whether to keep them
  or cancel them.

## Documents

- Upload manuals, invoices, certificates and photos from an item page, or from
  **Documents → Upload**. Accepted formats are PDF, images, text, and Office files. Files
  containing macros, programs or web pages are refused.
- **Upload new revision** replaces the current file but keeps every earlier revision. A
  service record keeps the exact revision of the invoice that was attached to it.
- **View** opens PDFs and images in the browser. Other files are downloaded.

## History

- **Add note or work** records a dated note, observation, service, repair, inspection,
  installation or purchase. Each entry can have a provider, a cost and attachments.
- You cannot edit an entry. Choose **Correct…** and give a reason: the correction becomes the
  current version, and the original stays visible under *Version history*.
- To act on an observation, tick **Create a follow-up task**, or open the entry and choose
  **Create task from this**.

## Tasks

- A task belongs to an item. It can have a due date, an assignee (an editor or owner) and
  reminders. Overdue tasks are marked "Overdue" in text, not just in colour.
- **Complete…** records the date the work was actually done, the kind of work, the provider,
  notes, the cost and any invoice. This creates a history entry.
- **Skip…** and **Cancel task…** both need a reason. They are recorded in history but never
  count as a completed service.
- **Booking tasks.** On a service task, choose **Add booking task**. Completing the booking
  ("engineer booked for the 14th") does *not* complete the service. Complete the service
  itself when the work is done.
- A completed task cannot be reopened. Use **Create follow-up task**, or correct its history
  entry.

## Recurring schedules

Open an item and choose **Add schedule**. There are two modes:

- **Fixed calendar.** The task is due on set dates, every N days, weeks, months or years
  from the first due date. Completing late does not move later dates.
  - A schedule anchored on the 31st falls on the last day of shorter months, then returns to
    the 31st.
  - A schedule anchored on 29 February falls on 28 February in non-leap years.
  - Occurrences are created 90 days ahead.
- **After completion.** The next task is due N units after the day the previous one was
  actually completed. There is only ever one open occurrence. If you skip it, you give the
  next due date or pause the schedule.

The form previews upcoming dates as you type.

**Changing a schedule** only affects occurrences in the future that have not been started.
You see which dates will change before you confirm. Completed, skipped and in-progress
occurrences are never changed. To move a single occurrence, edit that task's due date; the
schedule itself stays as it is.

**Disabling a schedule** stops new occurrences and their reminders. When you re-enable it,
you see a preview of any occurrences missed in the meantime and can skip them. To skip
several occurrences at once, for example while you are away, use **Skip several
occurrences** on the schedule page.

## Reminders

- **Default.** Seven days before the due date at 09:00 household time, sent to the assignee,
  or to all editors and owners if nobody is assigned.
- **Changing them.** Add more reminders (for example, on the due date itself), choose who
  receives them, restrict them to certain channels, or switch reminders off for a task.
- **Overdue tasks** are not reminded repeatedly.
- **After a delay.** If reminders were held up, for example because the server was off, you
  get one catch-up summary per channel rather than a flood of messages.
- **Failures.** Messages that fail repeatedly are shown on your dashboard and on the
  Notifications page.

## Search

The search box looks through item names, descriptions, tags, model and serial numbers,
history notes, document names and tasks. It does not search inside document contents.

## House manual

**Dashboard → House manual** (or **Household → House manual**) produces a complete printed
manual for someone who doesn't use HomeFabric, such as a buyer, tenant, house-sitter or
tradesperson. The manual contains:

- **Contents**: organised by property, floor and room, then systems, policies and
  agreements, and finally anything not yet placed. Every entry is numbered (for example
  1.2.1.1).
- **At a glance**: maintenance due in the next 90 days, all recurring maintenance, and every
  policy with its renewal date.
- **Map of the home**: colour-coded charts of where things are and how systems fit
  together. Each box also names its kind, so the chart still works printed in black and
  white.
- **A chapter for each room**, with a page for each item in it. An item page shows its
  location, every system it belongs to, its details, photos, documents, recurring and open
  tasks, related items and recent history.
- **Cross-references.** Each item is printed once. Wherever else it is mentioned, it appears
  with its number: a boiler in the kitchen that is part of both Central Heating and Hot
  Water is printed in the Kitchen chapter, and both system chapters point to it.

To get the manual:

- **View and print**: opens the manual. Press **Print or save as PDF**, and turn on
  "Background graphics" so the colours print.
- **Download as a single HTML file**: one file that works offline in any browser, with
  small photos built in. Open it and print it to make a PDF.
- **Download with documents (ZIP)**: owners only. Contains the manual plus every document it
  mentions, such as manuals, certificates and invoices, linked so they open straight from the
  manual.

Options let you choose how much history to include, whether to show photos, and whether
each item starts on a new printed page. The manual is a snapshot, not a backup.

## Owners: members and data

- **Household → Members → Invite someone.** Choose a role. If email is set up, the link is
  emailed; otherwise copy the link and send it yourself. Links work once and expire after 7
  days.
- **Change role / Remove access.** Removing access signs the person out immediately. Their
  past entries keep their name. A household always needs at least one active owner.
- **Household → Settings.** Change the name, timezone and default currency. Changing the
  timezone does not move any due dates.
- **Household → Export data.** Downloads everything as JSON, CSV and the original files.
  Passwords and notification credentials are not included.
