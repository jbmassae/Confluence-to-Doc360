# Confluence to Document360 Migration

Migrates Confluence pages into Document360 articles, converting
Confluence-specific elements (callouts, code blocks, accordions, images,
tables, links) into their Document360 equivalents.

Full SOP, the element-mapping table, and accumulated lessons
learned/known API quirks live in
[`workflows/confluence_to_doc360_migration.md`](workflows/confluence_to_doc360_migration.md) -
read that for anything beyond the quick start below.

## Setup

Requires Python 3.14. Create and populate the virtualenv from a native
**PowerShell** session (not WSL/Git-Bash - `.venv\Scripts\python.exe` needs
to be a real Windows executable):

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copy your credentials into `.env` (never committed - see `.gitignore`):

```
CONFLUENCE_BASE_URL=https://<site>.atlassian.net/wiki
CONFLUENCE_EMAIL=<email>
CONFLUENCE_API_TOKEN=<Atlassian API token>

DOC360_API_KEY=<Document360 API token>
DOCUMENT360_PROJECT_ID=<target Document360 workspace/project version id>
DOCUMENT360_USER_ID=<team member id to attribute articles to>
DOC360_DEFAULT_CATEGORY_PATH=<optional - default destination category>
```

## Usage

```powershell
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python.exe tools\migrate_page.py --url "<confluence-page-url>" --category "Category path & ID"
```
### Doc360 Project & Category ID
* Category Name should include path and category ID. For example "document/kb/view/e1234567-e123-123f-ab12-e12cf3aba567"
* The path is after the project ID/document/workspace_name, to get path select the destination category. The URL format is "https://portal.document360.io/<PROJECT_ID>/document/<WORKSPACE_NAME>/view/>CATEGORY_ID>"
* If you only put the name and it's not a top level category, it will create a new category with that name.

`--page_id <id>` works instead of `--url` if you have the numeric
Confluence page id handy. `--category` is optional - it falls back to
`DOC360_DEFAULT_CATEGORY_PATH` in `.env`, then prompts interactively.
Categories support `/`-separated nesting (`Parent/Child`) or a raw
category id/portal URL. Images are uploaded to the Document360 Drive
under `Images/Documentation/<category>`.

Run `tools\migrate_page.py --help` for the full option list.

## What gets converted

| Confluence | Document360 |
|---|---|
| `info`/`tip`/`warning`/`note` macros | Native Document360 callout blocks (Info/Success/Error/Warning) |
| `code` macro | Fenced code block with language |
| `expand` macro | Native Document360 Accordion block |
| `panel` macro | Info callout |
| Images (`ac:image`) | Uploaded to Document360 Drive, `<img>` rewritten to point at it |
| Tables, headings, lists, links | Passed through as standard HTML (H1 demoted to H2 - Document360 renders the article title as H1) |

See the workflow doc for the complete mapping table and any macros not yet handled.

## Project structure

```
tools/          # Python scripts: Confluence client, Document360 client, converter, CLI orchestrator
workflows/      # Full migration SOP and lessons-learned log
.claude/skills/ # Claude Code skill wrapping this tool
.tmp/           # Disposable scratch/debug scripts - not part of the tool
.env            # Credentials (gitignored)
```

## Known limitations

- One page per run - no bulk space/multi-page migration yet.
- No update/dedupe - re-running on the same page creates a duplicate article.
- Non-image attachments (PDFs, etc.) and diagram macros (e.g. `drawio`) aren't migrated.
- Internal Confluence page links are converted to plain text with a warning, not re-linked.
