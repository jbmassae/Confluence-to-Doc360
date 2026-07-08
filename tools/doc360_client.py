"""Deterministic client for Document360's v2 API: categories, articles, and Drive.

Endpoint/header details verified against apidocs.document360.com in July 2026:
- Auth header: api_token
- Base URL: https://apihub.document360.io/v2
"""
import os
import re

import requests

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class Doc360Error(RuntimeError):
    pass


class Doc360Client:
    def __init__(self, api_key=None, project_version_id=None, base_url=None, user_id=None):
        self.api_key = api_key or os.environ["DOC360_API_KEY"]
        self.project_version_id = (
            project_version_id
            or os.environ.get("DOCUMENT360_PROJECT_ID")
            or os.environ["DOC360_PROJECT_VERSION_ID"]
        )
        self.base_url = (base_url or os.environ.get("DOC360_API_BASE", "https://apihub.document360.io/v2")).rstrip("/")
        self._user_id = user_id or os.environ.get("DOCUMENT360_USER_ID") or os.environ.get("DOC360_USER_ID")

        self.session = requests.Session()
        self.session.headers["api_token"] = self.api_key
        self.session.headers["Accept"] = "application/json"

    def _unwrap(self, resp):
        resp.raise_for_status()
        data = resp.json()
        if data.get("success") is False:
            raise Doc360Error(f"Document360 API error: {data.get('errors')}")
        return data

    def _get(self, path, **kw):
        return self._unwrap(self.session.get(f"{self.base_url}{path}", **kw))

    def _post(self, path, **kw):
        return self._unwrap(self.session.post(f"{self.base_url}{path}", **kw))

    # --- Team ---

    def get_default_user_id(self):
        if self._user_id:
            return self._user_id
        data = self._get("/teams")
        users = data.get("result") or data.get("data") or []
        if not users:
            raise Doc360Error(
                "No Document360 team accounts found; set DOCUMENT360_USER_ID in .env explicitly."
            )
        self._user_id = users[0]["user_id"]
        return self._user_id

    # --- Categories ---

    def list_categories(self, exclude_articles=True):
        data = self._get(
            f"/ProjectVersions/{self.project_version_id}/categories",
            params={"excludeArticles": exclude_articles},
        )
        return data["data"]

    def get_category(self, category_id):
        data = self._get(f"/Categories/{category_id}")
        return data["data"]

    def _find_category_by_id(self, category_id, categories=None):
        categories = self.list_categories() if categories is None else categories

        def walk(cats):
            for cat in cats:
                if cat["id"] == category_id:
                    return cat
                found = walk(cat.get("child_categories") or [])
                if found:
                    return found
            return None

        return walk(categories)

    def resolve_category(self, value):
        """Resolve a category from either a raw/embedded category id (eg. one
        copied out of a Document360 portal URL like
        '.../document/kb/category/674c0b3e-e66e-4773-b4a8-ddec5e1b98df') or a
        '/'-separated category name path (eg. 'Parent/Child'), creating any
        missing name-path segments. Returns the category dict.

        A category id that exists but belongs to a different project version
        than self.project_version_id raises Doc360Error - creating an article
        would otherwise silently land in the wrong workspace.
        """
        uuid_match = _UUID_RE.search(value)
        if uuid_match:
            category_id = uuid_match.group(0)
            category = self.get_category(category_id)
            if not self._find_category_by_id(category_id):
                raise Doc360Error(
                    f"Category '{category.get('name')}' ({category_id}) does not belong to "
                    f"project version {self.project_version_id}; check DOCUMENT360_PROJECT_ID "
                    f"and the category id/path"
                )
            return category
        return self.find_or_create_category_path(value)

    def find_category(self, name, categories=None):
        categories = self.list_categories() if categories is None else categories

        def walk(cats):
            for cat in cats:
                if cat["name"].strip().lower() == name.strip().lower():
                    return cat
                found = walk(cat.get("child_categories") or [])
                if found:
                    return found
            return None

        return walk(categories)

    def create_category(self, name, parent_category_id=None):
        payload = {"name": name, "project_version_id": self.project_version_id}
        if parent_category_id:
            payload["parent_category_id"] = parent_category_id
        data = self._post("/Categories", json=payload)
        return data["data"]

    def find_or_create_category(self, name):
        existing = self.find_category(name)
        if existing:
            return existing
        return self.create_category(name)

    def find_or_create_category_path(self, path):
        """Resolve a '/'-separated category path (eg. 'Parent/Child'), creating
        any missing segments as nested categories. A single segment behaves
        exactly like find_or_create_category. Returns the leaf category dict.
        """
        segments = [s.strip() for s in path.split("/") if s.strip()]
        if not segments:
            raise Doc360Error(f"Empty Document360 category path: {path!r}")

        current_level = self.list_categories()
        parent_id = None
        category = None
        for segment in segments:
            category = next(
                (c for c in current_level if c["name"].strip().lower() == segment.lower()),
                None,
            )
            if category is None:
                category = self.create_category(segment, parent_category_id=parent_id)
                category["child_categories"] = []
            parent_id = category["id"]
            current_level = category.get("child_categories") or []
        return category

    # --- Drive ---

    def list_drive_folders(self):
        data = self._get("/Drive/Folders")
        return data["data"]

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
        payload = {"title": title, "user_id": self.get_default_user_id()}
        if parent_folder_id:
            payload["parent_folder_id"] = parent_folder_id
        data = self._post("/Drive/Folders", json=payload)
        folder = data["data"]
        # Document360 quirk: the create response uses media_folder_id/
        # media_folder_title, while GET /Drive/Folders (and find_drive_folder)
        # use id/title for the same resource - normalize to the list shape.
        return {
            "id": folder["media_folder_id"],
            "title": folder["media_folder_title"],
            "parent_folder_id": folder.get("parent_media_folder_id"),
            "sub_folders": [],
            "order": folder.get("order"),
        }

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
        files = [("files", (filename, content_bytes, content_type or "application/octet-stream"))]
        params = {"DriveFolderId": folder_id, "UserId": self.get_default_user_id()}
        resp = self.session.post(f"{self.base_url}/Drive/Folders/Files", params=params, files=files)
        data = self._unwrap(resp)
        return data["data"][0]

    # --- Articles ---

    def create_article(self, title, category_id, html_content, order=None):
        payload = {
            "title": title,
            "project_version_id": self.project_version_id,
            "user_id": self.get_default_user_id(),
            "category_id": category_id,
            "content": html_content,
            "content_type": 1,  # WYSIWYG/HTML
            "editor_version": 2,  # Classic editor - HTML content type requires it
        }
        if order is not None:
            payload["order"] = order
        data = self._post("/Articles", json=payload)
        return data["data"]
