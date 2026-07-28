---
name: confluence-to-doc360-migration
description: Migrates a Confluence page into a Document360 article using this repo's tool (tools/migrate_page.py), converting Confluence callouts/code blocks/accordions/images/tables/links into their Document360 equivalents. Use when asked to migrate, copy, convert, or port a Confluence page/article into Document360. Triggers - "migrate this Confluence page", "convert to Document360", "move this page to Doc360", "port this article", "send this to Doc360".
---

# Confluence to Document360 migration

Full SOP, the Confluence->Document360 element-mapping table, and accumulated
lessons learned/known quirks live in
`workflows/confluence_to_doc360_migration.md` - read it before running
anything if this is the first migration of the session, or if something
fails in a way not already covered below.

## Gather inputs
1. **Source**: a Confluence page URL, or a bare numeric Confluence page id.
   If the user gave neither, ask.
2. **Destination category**: a Document360 category name (`/`-separated for
   nesting, eg. `Parent/Child`), or a category id/portal-URL. If not given,
   it's optional on the command line - the tool falls back to
   `DOC360_DEFAULT_CATEGORY_PATH` in `.env`, then prompts interactively if
   that's unset too. Don't ask the user for it up front unless they clearly
   want a specific category other than the default.

## Run it
From a native **PowerShell** session (never WSL/Git-Bash - see "Environment
setup" in `CLAUDE.md` if `.venv` throws an executable-path error):

```powershell
cd "C:\Users\jason.massae\OneDrive - VAST Data\AI-Proj\Confluence to Doc360"
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe tools\migrate_page.py --page_id <id> --category "<category>"
```

Use `--url "<confluence-page-url>"` instead of `--page_id` if that's what
was given. Add `--drive-folder "<name>"` only if images should land in a
Drive folder leaf name different from the category (they're nested under
`Images/Documentation/<name>` either way - see `DRIVE_IMAGE_ROOT` in
`tools/migrate_page.py`).

## After it runs
- Report the created article id/title/category and image upload count from
  the script's own summary output.
- **Always surface every warning the script prints** (unrecognized macros,
  broken internal links, missing images) - these need human review, don't
  silently swallow them.
- Each run creates a **new** article - there's no update/dedupe path yet.
  If re-migrating the same page, say so explicitly rather than assuming
  it'll overwrite the previous one.
- If something fails, check `workflows/confluence_to_doc360_migration.md`'s
  "Known quirks / lessons learned" section first - several Document360 API
  inconsistencies (mismatched field names between read/write endpoints,
  content-type being silently overridden, etc.) are already documented
  there with the fix. If you find and fix a *new* one, add it there too,
  per this repo's `CLAUDE.md` self-improvement loop.
