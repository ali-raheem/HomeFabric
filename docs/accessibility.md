# Accessibility (target: WCAG 2.2 AA for core flows)

## Built in

- Semantic landmarks (`header`, `nav`, `main`, `footer`), a skip link, one `h1` per page, and
  sections labelled by their headings.
- Every form control has a visible `<label>` (or legend). Help text and errors are linked
  with `aria-describedby` / `aria-invalid`. Errors are written out in text, not just shown in
  colour.
- A visible 3 px focus outline on every interactive element. Buttons and form controls are
  at least 32 px high (2.6 rem for primary controls), above the WCAG 2.2 target-size minimum
  of 24 px.
- Status is always written out in text: "Overdue by 3 days", "Disabled", "Failed". Colour is
  never the only signal. Colour pairs meet a contrast ratio of at least 4.5:1 in light and
  dark modes.
- The layout reflows to 360 px with no horizontal page scrolling. Tables become labelled
  rows on small screens.
- Messages are announced through `role="status"` / `role="alert"` live regions. The explorer
  tree uses buttons with `aria-expanded`, `aria-controls` and a live region.
- Every feature works without JavaScript; htmx only adds convenience.
- Respects `prefers-color-scheme`.

## Automated checks (`tests/test_web.py`)

The suite crawls every page reachable as owner, editor, viewer and instance administrator,
and checks that:

- the page sets `lang`;
- it has a `main` landmark and an `h1`;
- every control is labelled;
- every image has alt text;
- it has a viewport meta tag;
- it loads no external assets.

## Manual checklist before each release (A16)

Use a keyboard only, then repeat at a 360 px viewport width (browser dev tools):

1. Sign in, then switch household.
2. Create a property, a room and a piece of equipment located in that room.
3. Add a *part of* relationship to a system. Expand both trees in **Explore**.
4. Upload a document to the item, add a revision, then download it.
5. Add an observation and create a task from it.
6. Create a fixed schedule and confirm the preview is announced. Edit it and confirm the
   change preview.
7. Complete a task with an attached invoice. Skip a task with a reason.
8. Invite a member and copy the link. Change their role.
9. Add a notification channel and send a test.
10. Check with a screen reader (NVDA on Windows, VoiceOver on macOS) that every step is
    announced sensibly.
