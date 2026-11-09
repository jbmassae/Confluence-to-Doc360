"""Migrate a single Confluence page into Document360.

Usage:
    python tools/migrate_page.py --url <confluence-page-url> [--category "Category name or ID"] [--drive-folder "Folder name"]
    python tools/migrate_page.py --page_id <confluence-page-id> [--category "Category name or ID"] [--drive-folder "Folder name"]

Exactly one of --url or --page_id is required. --page_id skips URL parsing
entirely and fetches the page directly by its numeric Confluence content id.

--category picks both the destination workspace and category, as either:
- a Document360 category id (a raw UUID, or a full portal URL/path that
  ends in one, eg. ".../document/kb/category/674c0b3e-e66e-4773-b4a8-ddec5e1b98df");
  the article goes to whichever workspace that category belongs to, or
- a '/'-separated "Workspace/Parent/Child" name path, where the first
  segment is a workspace name or URL slug (eg. "KBs & Tutorials/Staging");
  missing category segments are created.

If --category is omitted, DOC360_DEFAULT_CATEGORY_PATH from .env is used;
if that's also unset, the category is asked for interactively. The
resolved category's name is also used as the Drive folder name for the
article's images (nested under DRIVE_IMAGE_ROOT, see below), unless
--drive-folder overrides just that leaf folder name.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

from confluence_client import ConfluenceClient
from converter import convert, referenced_attachment_filenames
from doc360_client import Doc360Client

# Fixed parent path images are nested under in Document360 Drive, per
# instruction 2026-07-08: "Images should be placed under
# My Drive\Images\Documents\<category>" - "My Drive" is just the Drive
# root, and the real folder is named "Documentation" (confirmed via
# GET /v2/Drive/Folders; there is no folder actually named "Documents").
DRIVE_IMAGE_ROOT = "Images/Documentation"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Confluence page URL")
    source.add_argument("--page_id", help="Confluence page id (skips URL parsing)")
    parser.add_argument(
        "--category",
        help="Destination Document360 category id/portal URL, or 'Workspace/Category/...' name path",
    )
    parser.add_argument(
        "--drive-folder",
        help=f"Leaf Drive folder name for images, nested under {DRIVE_IMAGE_ROOT}/ "
        "(defaults to the category's name)",
    )
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(project_root, ".env"))

    category_value = args.category or os.environ.get("DOC360_DEFAULT_CATEGORY_PATH") or ""
    category_value = category_value.strip()
    if not category_value:
        category_value = input(
            "No destination category provided. Enter the Document360 category "
            "(category id/portal URL, or name path like 'Workspace/Parent/Child') to file this "
            "article under - also used as the Drive folder name for its images: "
        ).strip()
        if not category_value:
            print("A category is required. Aborting.")
            sys.exit(1)

    confluence = ConfluenceClient()
    doc360 = Doc360Client()

    print(f"Resolving Document360 category '{category_value}'...")
    category = doc360.resolve_category(category_value)
    category_name = category["name"]
    workspace_id = category["workspace_id"]
    workspace_name = next(
        (w["name"] for w in doc360.list_workspaces() if w["id"] == workspace_id), workspace_id
    )
    drive_folder_name = args.drive_folder or category_name
    print(f"Resolved category: '{category_name}' (id: {category['id']}) in workspace '{workspace_name}'")

    if args.page_id:
        page_id = args.page_id
        print(f"Using Confluence page id: {page_id}")
    else:
        print(f"Resolving Confluence page from URL: {args.url}")
        page_id = confluence.resolve_page_id(args.url)
    page = confluence.get_page(page_id)
    print(f"Fetched page {page_id}: '{page['title']}'")

    referenced = referenced_attachment_filenames(page["storage_html"])
    image_map = {}
    if referenced:
        print(f"Page references {len(referenced)} image attachment(s); fetching attachment list...")
        attachments = confluence.list_attachments(page_id)
        by_name = {a["filename"]: a for a in attachments}

        drive_folder_path = f"{DRIVE_IMAGE_ROOT}/{drive_folder_name}"
        print(f"Finding/creating Document360 Drive folder '{drive_folder_path}'...")
        drive_folder = doc360.find_or_create_drive_folder_path(drive_folder_path)

        for filename in sorted(referenced):
            attachment = by_name.get(filename)
            if not attachment:
                print(f"  ! Referenced image '{filename}' has no matching Confluence attachment; skipping")
                continue
            print(f"  - Downloading '{filename}' from Confluence...")
            content = confluence.download_attachment(attachment)
            print(f"    Uploading to Document360 Drive folder '{drive_folder_path}'...")
            uploaded = doc360.upload_file_to_drive(
                drive_folder["id"], filename, content, attachment.get("media_type")
            )
            image_map[filename] = uploaded["file_url"]

    print("Converting Confluence content to Document360 HTML...")
    html_content, warnings = convert(page["storage_html"], image_map.get)

    print("Creating Document360 article...")
    article = doc360.create_article(
        title=page["title"],
        category_id=category["id"],
        workspace_id=workspace_id,
        html_content=html_content,
    )

    print()
    print("=== Migration complete ===")
    print(f"Article: {article.get('title')} (id: {article.get('id')})")
    print(f"Workspace: {workspace_name} (id: {workspace_id})")
    print(f"Category: {category_name} (id: {category['id']})")
    print(f"Images uploaded: {len(image_map)}/{len(referenced)}")
    if warnings:
        print(f"\n{len(warnings)} warning(s) - review the article for these:")
        for w in warnings:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
