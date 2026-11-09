"""Deterministic client for Document360's v3 API: categories, articles, and Drive.

Endpoint/header details verified against apidocs.document360.com (and live
calls) in October 2026:
- Auth header: X-API-Key (a v3 "API key", d360_sk_...; v1/v2 api_tokens
  are rejected by v3)
- Base URL: https://apihub.document360.io/v3 (US/CA projects use
  apihub.us./apihub.ca.; the region is auto-detected from which host
  accepts the key unless DOC360_API_BASE is set)
- Every path is scoped to /projects/{project_id}; what v2 called a
  "project version" is a v3 "workspace" (same id)
"""
import os
import re

import requests

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

_REGIONAL_HOSTS = (
    "https://apihub.document360.io",  # Europe (default)
    "https://apihub.us.document360.io",
    "https://apihub.ca.document360.io",
)


class Doc360Error(RuntimeError):
    pass


class Doc360Client:
    def __init__(self, api_key=None, project_id=None, base_url=None, user_id=None):
        self.api_key = api_key or os.environ["DOC360_API_KEY_V3"]
        self._user_id = user_id or os.environ.get("DOCUMENT360_USER_ID") or os.environ.get("DOC360_USER_ID")

        self.session = requests.Session()
        self.session.headers["X-API-Key"] = self.api_key
        self.session.headers["Accept"] = "application/json"

        base_url = base_url or os.environ.get("DOC360_API_BASE")
        if base_url:
            self.base_url = base_url.rstrip("/")
            projects = self._unwrap(self.session.get(f"{self.base_url}/projects"))["data"]
        else:
            self.base_url, projects = self._discover_region()
        project_ids = {p["id"] for p in projects}

        # The workspace is not configured here - it comes from the category
        # each migration targets (see resolve_category). DOCUMENT360_PROJECT_ID
        # held the v2 project version (= v3 workspace) id in older .env
        # blocks, so it is only used when it really is a v3 project id.
        env_project_id = os.environ.get("DOCUMENT360_PROJECT_ID")
        self.project_id = (
            project_id
            or os.environ.get("DOCUMENT360_V3_PROJECT_ID")
            or (env_project_id if env_project_id in project_ids else None)
            or self._single_project_id(projects)
        )
        self.project_url = f"{self.base_url}/projects/{self.project_id}"
        self._workspaces = None

    def _unwrap(self, resp):
        # v3 errors are RFC 7807 problem+json bodies; surface their detail
        # instead of a bare HTTPError.
        if not resp.ok:
            try:
                body = resp.json()
            except ValueError:
                body = {"title": resp.reason or "(empty response body)"}
            raise Doc360Error(
                f"Document360 API error {resp.status_code} on {resp.request.method} {resp.url}: "
                f"{body.get('detail') or body.get('title')} {body.get('errors') or ''}"
            )
        data = resp.json()
        if data.get("success") is False:
            raise Doc360Error(f"Document360 API error: {data.get('errors')}")
        return data

    def _get(self, path, **kw):
        return self._unwrap(self.session.get(f"{self.project_url}{path}", **kw))

    def _post(self, path, **kw):
        return self._unwrap(self.session.post(f"{self.project_url}{path}", **kw))

    def _get_all(self, path, params=None):
        """GET every page of a paginated v3 list endpoint (default page size is 25)."""
        items, page = [], 1
        while True:
            data = self._get(path, params={**(params or {}), "page": page})
            items.extend(data["data"])
            if not (data.get("pagination") or {}).get("has_more"):
                return items
            page += 1

    def _discover_region(self):
        """v3 is served per region and a key only authenticates against its
        own project's host (others return 401), so try each in turn."""
        for host in _REGIONAL_HOSTS:
            resp = self.session.get(f"{host}/v3/projects")
            if resp.status_code != 401:
                return f"{host}/v3", self._unwrap(resp)["data"]
        raise Doc360Error(
            f"DOC360_API_KEY_V3 was rejected (401) by every Document360 region "
            f"({', '.join(_REGIONAL_HOSTS)}); check the key is a v3 API key (d360_sk_...) "
            f"and hasn't expired or been revoked."
        )

    def _single_project_id(self, projects):
        if len(projects) != 1:
            raise Doc360Error(
                f"API key can access {len(projects)} Document360 projects; set "
                f"DOCUMENT360_V3_PROJECT_ID in .env explicitly (GET /v3/projects lists them)."
            )
        return projects[0]["id"]

    # --- Workspaces ---

    def list_workspaces(self):
        if self._workspaces is None:
            self._workspaces = self._get_all("/workspaces")
        return self._workspaces

    def find_workspace(self, name):
        """Match a workspace by name or URL slug (the 'kb' in a portal URL like
        '.../document/kb/category/<id>'), case-insensitively."""
        name = name.strip().lower()
        return next(
            (
                w for w in self.list_workspaces()
                if w["name"].strip().lower() == name or (w.get("slug") or "").lower() == name
            ),
            None,
        )

    # --- Team ---

    def get_default_user_id(self):
        if self._user_id:
            return self._user_id
        users = self._get("/users")["data"]
        if not users:
            raise Doc360Error(
                "No Document360 team accounts found; set DOCUMENT360_USER_ID in .env explicitly."
            )
        self._user_id = users[0]["id"]
        return self._user_id

    # --- Categories ---

    def list_categories(self, workspace_id):
        return self._get_all(f"/workspaces/{workspace_id}/categories")

    def get_category(self, category_id):
        data = self._get(f"/categories/{category_id}")
        return data["data"]

    def resolve_category(self, value):
        """Resolve the destination category - and with it the workspace - from
        either:

        - a raw/embedded category id (eg. one copied out of a Document360
          portal URL like '.../document/kb/category/674c0b3e-...'); the
          workspace is whichever one the category belongs to, or
        - a '/'-separated 'Workspace/Category/Sub-category' name path, where
          the first segment is a workspace name or URL slug; missing category
          segments are created.

        Returns the category dict, always including its 'workspace_id'.
        """
        # A full portal URL also embeds the project id
        # (portal.document360.io/<project_id>/document/<ws>/category/<id>);
        # the category id is always the last one.
        uuids = _UUID_RE.findall(value)
        if uuids:
            return self.get_category(uuids[-1])

        segments = [s.strip() for s in value.split("/") if s.strip()]
        workspace = self.find_workspace(segments[0]) if segments else None
        if workspace is None:
            listing = "; ".join(f"{w['name']} (slug: {w.get('slug')})" for w in self.list_workspaces())
            raise Doc360Error(
                f"Category path {value!r} must start with a workspace name or slug, "
                f"eg. 'KBs & Tutorials/Staging', or be a category id/portal URL. "
                f"Workspaces: {listing}"
            )
        if len(segments) < 2:
            raise Doc360Error(f"Category path {value!r} names a workspace but no category")
        return self.find_or_create_category_path(workspace["id"], "/".join(segments[1:]))

    def create_category(self, name, workspace_id, parent_category_id=None):
        payload = {
            "name": name,
            "workspace_id": workspace_id,
            "user_id": self.get_default_user_id(),  # required for API-key (M2M) callers
        }
        if parent_category_id:
            payload["parent_category_id"] = parent_category_id
        data = self._post("/categories", json=payload)
        return data["data"]

    def find_or_create_category_path(self, workspace_id, path):
        """Resolve a '/'-separated category path (eg. 'Parent/Child') within a
        workspace, creating any missing segments as nested categories. Returns
        the leaf category dict, with 'workspace_id' set (the workspace
        category-tree listing omits it).
        """
        segments = [s.strip() for s in path.split("/") if s.strip()]
        if not segments:
            raise Doc360Error(f"Empty Document360 category path: {path!r}")

        current_level = self.list_categories(workspace_id)
        parent_id = None
        category = None
        for segment in segments:
            category = next(
                (c for c in current_level if c["name"].strip().lower() == segment.lower()),
                None,
            )
            if category is None:
                category = self.create_category(segment, workspace_id, parent_category_id=parent_id)
                category["child_categories"] = []
            parent_id = category["id"]
            current_level = category.get("child_categories") or []
        category["workspace_id"] = workspace_id
        return category

    # --- Drive ---

    def list_drive_folders(self):
        return self._get_all("/drive/folders")

    def find_drive_folder(self, title, folders=None):
        folders = self.list_drive_folders() if folders is None else folders

        def walk(fs):
            for folder in fs:
                if folder["title"].strip().lower() == title.strip().lower():
                    return folder
                found = walk(folder.get("sub_folders") or [])
                if found:
                    return found
            return None

        return walk(folders)

    def create_drive_folder(self, title, parent_folder_id=None):
        # v3 takes "name" on create but returns the folder as id/title, the
        # same shape GET /drive/folders uses.
        payload = {"name": title}
        if parent_folder_id:
            payload["parent_folder_id"] = parent_folder_id
        data = self._post("/drive/folders", json=payload)
        folder = data["data"]
        folder.setdefault("sub_folders", [])
        return folder

    def find_or_create_drive_folder(self, title):
        existing = self.find_drive_folder(title)
        if existing:
            return existing
        return self.create_drive_folder(title)

    def find_or_create_drive_folder_path(self, path):
        """Resolve a '/'-separated Drive folder path (eg. 'Images/Documentation/Staging'),
        creating any missing segments nested under their parent. Each segment
        is matched only among its parent's direct sub_folders (not a global
        name search), so eg. two different parents can each have a "Staging"
        child without colliding. Returns the leaf folder dict.
        """
        segments = [s.strip() for s in path.split("/") if s.strip()]
        if not segments:
            raise Doc360Error(f"Empty Drive folder path: {path!r}")

        current_level = self.list_drive_folders()
        parent_id = None
        folder = None
        for segment in segments:
            folder = next(
                (f for f in current_level if f["title"].strip().lower() == segment.lower()),
                None,
            )
            if folder is None:
                folder = self.create_drive_folder(segment, parent_folder_id=parent_id)
                folder["sub_folders"] = []
            parent_id = folder["id"]
            current_level = folder.get("sub_folders") or []
        return folder

    def upload_file_to_drive(self, folder_id, filename, content_bytes, content_type=None):
        files = {"file": (filename, content_bytes, content_type or "application/octet-stream")}
        data = self._post(f"/drive/folders/{folder_id}/files", files=files)
        return data["data"]

    # --- Articles ---

    def create_article(self, title, category_id, workspace_id, html_content, order=None):
        payload = {
            "title": title,
            "workspace_id": workspace_id,
            "user_id": self.get_default_user_id(),
            "category_id": category_id,
            "content": html_content,
            "content_type": "wysiwyg",  # HTML content
        }
        if order is not None:
            payload["order"] = order
        data = self._post("/articles", json=payload)
        return data["data"]
