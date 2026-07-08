# Workflow: Migrate a Confluence page to Document360

## Objective
Copy a single Confluence page's content into Document360 as a new article,
converting Confluence-specific elements (callouts, code blocks, images,
tables, links) into their Document360 equivalents, and placing any images
in the Document360 Drive under `Images/Documentation/<category>` (per
instruction 2026-07-08).

## Required inputs
- **Source URL**: any Confluence Cloud page URL (`/wiki/spaces/KEY/pages/ID/Title`,
  `/wiki/pages/viewpage.action?pageId=ID`, or a `/wiki/x/...` short link all work).
- **Destination category**: the Document360 category name to file the article
  under. If not provided on the command line, the tool prompts for it
  interactively - this same name is also used as the leaf Drive folder name
  for the article's images (nested under `Images/Documentation/`, see
  `DRIVE_IMAGE_ROOT` in `migrate_page.py`) unless `--drive-folder` overrides
  just that leaf name.

## Tool
`tools/migrate_page.py` (uses `tools/confluence_client.py`, `tools/doc360_client.py`,
`tools/converter.py`).

```
.venv\Scripts\python.exe tools\migrate_page.py --url "<confluence-page-url>" --category "Category Name"
```

Optional: `--drive-folder "Folder Name"` if images should go somewhere other
than a folder matching the category name.

## What it does
1. Resolves the Confluence page id from the URL and fetches its storage-format body.
2. Finds every image attachment actually referenced in the page body.
3. Finds-or-creates the destination Document360 category, and finds-or-creates
   a matching Drive folder for images.
4. Downloads each referenced image from Confluence and uploads it to that Drive folder.
5. Converts the Confluence storage HTML to Document360 HTML (see mapping below),
   rewriting image `src` attributes to the newly-uploaded Document360 Drive URLs.
6. Creates the article in Document360 (`content_type: 1`, raw HTML) under the
   resolved category.
7. Prints a summary plus any conversion warnings (unrecognized macros, broken
   internal links, missing images) - **always read these before publishing**.

## Confluence -> Document360 element mapping
| Confluence | Document360 |
|---|---|
| `info` macro | Native `infoBox` callout |
| `tip` macro | Native `successBox` callout |
| `warning` macro | Native `errorBox` callout |
| `note` macro | Native `warningBox` callout (Document360 has no native "Note" type - see below) |
| `code` macro | `<pre><code class="language-X">` |
| `expand` macro | Native Document360 Accordion block (single collapsible item, titled) |
| `panel` macro | Rendered as an `infoBox` callout titled with the macro's own title, flagged as a warning to review |
| `ac:image` + attachment | `<img>` pointing at the uploaded Document360 Drive file |
| `ac:image` + external URL | `<img>` unchanged |
| Internal page link (`ac:link` + `ri:page`) | Plain text span + warning (destination page doesn't exist in Document360; re-link manually) |
| `task-list` | Plain `<ul>` with ☑/☐ prefixes |
| `toc` macro | Dropped silently (Document360 doesn't need it) |
| Any other macro | Inner content kept, macro wrapper stripped, warning logged |

### Callouts: how the real markup was found (2026-07-08)
Document360's internal callout markup isn't publicly documented, and a
first attempt at a plausible-looking `<blockquote class="callout callout-info">`
with a `background-color` inline style **did not render any color** despite
being stored correctly (verified via `GET /v2/Articles/{id}` - the HTML was
byte-for-byte what was sent). The actual cause: Document360's Block editor
only applies its callout styling to blockquotes with a class name it
recognizes - a made-up class name doesn't match anything, regardless of
inline style.

The real markup was found by asking a human to color one callout of each
native type (Info/Warning/Error/Success) using Document360's own callout
picker in the portal editor, saving, then reading the article back via the
API. Document360's own generated HTML for a callout looks like:
```html
<blockquote class="infoBox" data-background="#ddf7ff" data-border="#006a8a"
  style="background:rgb(221, 247, 255);border-left:4px solid rgb(0, 106, 138);overflow:auto;">
  <div class="blockquote-title"><p>Title</p></div>
  <p>Content</p>
</blockquote>
```
`tools/converter.py`'s `NATIVE_CALLOUTS` dict has the confirmed class/color
pairs for all 4 types (`infoBox`/`warningBox`/`errorBox`/`successBox`).
Document360 only has these 4 native callout types - there is no "Note" type,
so `CONFLUENCE_MACRO_TO_DOC360` maps Confluence's `note` macro onto the
`warningBox` type (closest available; both are yellow/tan) rather than
inventing an unrecognized class name that risks not rendering, the same way
the original guess didn't.

**Verified working (2026-07-08)**: live migration of Confluence page
7815692341 into Staging as article `92c37ae0-cf88-461a-9c4e-582d061f6e6e`
produces the exact native `infoBox` markup shown above for all 4 `info`
callouts on that page (confirmed via `GET /v2/Articles/{id}` readback -
visual confirmation in the portal is still pending as of this write-up).

### Accordions: `expand` macro -> native Document360 Accordion block (2026-07-08)
Same reverse-engineering method as callouts: a human added one native
Accordion item via the portal editor to the same test article, saved, and
the real generated markup was read back via the API:
```html
<div class="details-wrapper details-wrapper_rendered" data-type="details-wrapper">
  <details data-opened="true" data-button-align="right" data-details-border="withBorder"
            data-expand-icon="arrow" data-lock="false">
    <summary><p>Title</p></summary>
    <div data-type="details-content"><p>Body</p></div>
  </details>
  <button class="details-arrow" type="details"></button>
</div>
```
It's a standard HTML5 `<details>`/`<summary>` pair wrapped in a
`div.details-wrapper`, plus a trailing empty `<button class="details-arrow">`
sibling that Document360's own CSS/JS uses to draw the expand arrow.
`tools/converter.py`'s `render_accordion()` reproduces this exactly.
Document360 also has an "FAQ" block (multiple accordion items grouped under
one `<h3>`, wrapped in `<editor360-faq>`) - not used here since a single
Confluence `expand` maps more directly onto one standalone Accordion item.

Note the Accordion block is a Document360 Block Editor **beta** feature
that needs enabling per-project via a Document360 Customer Success Manager;
this org already had it enabled.

**Verified working (2026-07-08)**: live migration of Confluence page
7853867017 ("Converged Cisco NX-OS - iBGP EVPN VXLAN", which uses `expand`
macros titled "Spine" and "Leaf", each wrapping a nested `code` macro) into
Staging as article `fbfda672-8702-4676-8008-56121fa56bd1` produced the
native accordion markup for both, with the nested code blocks converted
correctly inside `details-content`. That page also has one `drawio` macro
(a diagram), which is out of scope and correctly flagged as an unrecognized
macro rather than silently dropped.

Also discovered: `GET /v2/Articles/{id}` returns the body under
`html_content`, not `content` (the field name used when *creating* an
article via POST) - Document360's read and write schemas use different
field names for the same thing. Document360 also injects its own
`local-id`/`data-block-id` attributes onto elements once a human opens/saves
the article in the portal editor; harmless, ignore them.

## Environment (.env)
```
CONFLUENCE_BASE_URL=https://<site>.atlassian.net/wiki
CONFLUENCE_EMAIL=<email>
CONFLUENCE_API_TOKEN=<Atlassian API token>

DOC360_API_KEY=<Document360 API token>
DOCUMENT360_PROJECT_ID=<target workspace/project version id>
DOCUMENT360_USER_ID=<team member id to attribute articles to>
DOC360_DEFAULT_CATEGORY_PATH=<optional - default --category value; see migrate_page.py --help>
```

`DOCUMENT360_PROJECT_ID` and `DOCUMENT360_USER_ID` are the current variable
names (`doc360_client.py` also still accepts the older `DOC360_PROJECT_VERSION_ID`
/ `DOC360_USER_ID` names as a fallback if the new ones aren't set - the newer
names take priority when both are present). `DOCUMENT360_USER_ID` is
otherwise auto-picked from the first result of `GET /v2/teams` if omitted,
which is unlikely to be the account you want articles attributed to - set
it explicitly.

To find `DOCUMENT360_PROJECT_ID`, call `GET /v2/ProjectVersions` - the
response lists every workspace with its `id`, `version_code_name`, and
whether `is_main_version` is true.

## Setup
```
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```
(On this machine, Python 3.14 lives at `C:\Users\jason.massae\.local\bin\python3.14.exe`
and isn't on PATH by default - use its full path to create the venv.)

Always create/recreate the venv from a native **PowerShell** session, not a
WSL/Git-Bash/Unix-style shell - `.venv\Scripts\python.exe` needs to be a
real Windows executable. If a run ever fails with an error like
`did not find executable at '/usr/bin\python.exe'`, the venv was created
(or recreated) from a Unix-style shell - check `.venv\pyvenv.cfg`; if
`home`/`executable` show `/usr/bin/...` paths instead of a Windows path,
delete `.venv` entirely and recreate it per the commands above from
PowerShell.

## Known quirks / lessons learned
- **Callout background colors not appearing in Document360, even though the
  API stores them correctly (open issue, 2026-07-07):** `content_type: 1`
  (WYSIWYG/Classic editor) requested at article creation is silently
  downgraded to `content_type: 2` (Block editor) by this portal - confirmed
  by creating test articles with `content_type` 0/1/2 and reading them back;
  requesting 1 always comes back as 2. Despite that, `GET /v2/Articles/{id}`
  shows the raw HTML - including our `style="background-color:..."` on the
  blockquote - stored byte-for-byte unchanged regardless of content_type.
  So the color loss the user is seeing is happening somewhere in
  Document360's *rendering* (most likely the Block editor re-parses raw HTML
  into its own block schema on first open in the UI, or the public reader
  view sanitizes inline styles), not in what `tools/converter.py` produces
  or what gets POSTed. Unresolved without visual access to the Document360
  editor/portal (no browser tool available in this environment). Next step:
  have someone insert a native Info callout via the Document360 web editor
  into any article, then `GET` that article's `html_content`/block data to
  see the real markup Document360's own callout component emits, and make
  `converter.py` produce that instead of a styled blockquote.
- Document360's `GET /v2/teams` response wraps users under a `"result"` key,
  not `"data"` like every other endpoint - `doc360_client.get_default_user_id`
  already accounts for this.
- **`KeyError: 'id'` when a migration needs to create a new Drive folder
  (fixed 2026-07-08):** `POST /v2/Drive/Folders` (create) returns the new
  folder under `media_folder_id`/`media_folder_title`, while
  `GET /v2/Drive/Folders` (list) uses `id`/`title` for the exact same
  resource - yet another Document360 endpoint using a different field name
  for the same thing depending on whether you're reading or writing (see
  also the `html_content`/`content` quirk below). `create_drive_folder`
  now normalizes the create response to the list shape (`id`/`title`/
  `parent_folder_id`/`sub_folders`) so `find_or_create_drive_folder` always
  returns a consistent dict. This only surfaces the first time a migration
  targets a category/image-folder name that doesn't already exist in Drive -
  reusing an existing folder name (found via `GET`) never hit this bug.
- **Images must nest under `Images/Documentation/<category>` in Drive, not
  sit at the Drive root (instruction 2026-07-08):** confirmed via
  `GET /v2/Drive/Folders` that the real top-level structure is
  `Images > Documentation > <various category folders>` (eg. the existing
  "Staging" folder lives at exactly that nesting). "My Drive" in the
  instruction just means the Drive root; there is no folder literally named
  "Documents" - the real name is "Documentation". `doc360_client.py`'s
  `find_or_create_drive_folder_path()` walks/creates each path segment
  scoped to its parent (not a global name search, so a "Staging" folder
  elsewhere in Drive can't collide), and `migrate_page.py` always resolves
  images under `DRIVE_IMAGE_ROOT = "Images/Documentation"` plus the
  category/`--drive-folder` name as the leaf.
- Console output containing emoji (callout labels, ☑/☐) can throw
  `UnicodeEncodeError` on Windows terminals using the default `cp1252` code
  page. Run with `$env:PYTHONIOENCODING="utf-8"` set first if you hit this.
- A 401 from Confluence with an HTML "HTTP Status 401" Tomcat-style body
  (rather than a JSON error) means the Basic-auth credentials themselves are
  being rejected by Atlassian's edge - it is not a network/proxy issue
  (confirmed by getting a proper JSON 403 from the same host with no auth
  header at all). The response also carries `Server: AtlassianEdge` and
  `Www-Authenticate: OAuth realm=...`; that header is just Atlassian's
  generic auth challenge and does **not** mean Basic auth is disabled for
  the org - a third freshly-generated `CONFLUENCE_API_TOKEN` authenticated
  fine (2026-07-07) with the same email and the exact same request code, so
  the first two tokens were simply bad/expired/mistyped, not a policy block.
  If you hit this again: just regenerate the token first before suspecting
  OAuth or org policy.

## Edge cases not yet handled (extend here as they come up)
- Multi-page/space migrations (this tool does one page at a time).
- Confluence attachments beyond images referenced via `ac:image` (e.g. PDFs
  linked with `ac:link` + `ri:attachment`) are not uploaded or relinked yet.
- Nested/child pages are not recursively migrated.
