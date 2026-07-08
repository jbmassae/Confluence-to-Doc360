"""Deterministic client for reading pages and attachments out of Confluence Cloud.

Auth and endpoint details verified against the Confluence Cloud REST API
(GET /wiki/rest/api/content/{id}?expand=body.storage) in July 2026.
"""
import os
import re

import requests
from requests.auth import HTTPBasicAuth


class ConfluenceError(RuntimeError):
    pass


class ConfluenceClient:
    def __init__(self, base_url=None, email=None, api_token=None):
        base_url = base_url or os.environ["CONFLUENCE_BASE_URL"]
        email = email or os.environ["CONFLUENCE_EMAIL"]
        api_token = api_token or os.environ["CONFLUENCE_API_TOKEN"]

        base_url = base_url.rstrip("/")
        if base_url.endswith("/wiki"):
            self.site_base = base_url[: -len("/wiki")]
            self.wiki_base = base_url
        else:
            self.site_base = base_url
            self.wiki_base = f"{base_url}/wiki"
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(email, api_token)
        self.session.headers["Accept"] = "application/json"

    def resolve_page_id(self, url):
        """Extract a Confluence page id from any of the common URL shapes:
        - /wiki/spaces/KEY/pages/123456/Title
        - /wiki/pages/viewpage.action?pageId=123456
        - /wiki/x/AbCdEf (tiny link) - resolved by following the redirect
        """
        match = re.search(r"/pages/(\d+)(?:/|$)", url)
        if match:
            return match.group(1)

        match = re.search(r"[?&]pageId=(\d+)", url)
        if match:
            return match.group(1)

        resp = self.session.get(url, allow_redirects=True)
        resp.raise_for_status()

        match = re.search(r"/pages/(\d+)(?:/|$)", resp.url)
        if match:
            return match.group(1)
        match = re.search(r'name="ajs-page-id"\s+content="(\d+)"', resp.text)
        if match:
            return match.group(1)

        raise ConfluenceError(
            f"Could not determine a Confluence page id from URL: {url}"
        )

    def get_page(self, page_id):
        resp = self.session.get(
            f"{self.wiki_base}/rest/api/content/{page_id}",
            params={"expand": "body.storage,space,version"},
        )
        if resp.status_code == 404:
            raise ConfluenceError(f"Confluence page {page_id} not found (404)")
        resp.raise_for_status()
        data = resp.json()
        return {
            "id": data["id"],
            "title": data["title"],
            "space_key": data.get("space", {}).get("key"),
            "storage_html": data["body"]["storage"]["value"],
        }

    def list_attachments(self, page_id):
        attachments = []
        url = f"{self.wiki_base}/rest/api/content/{page_id}/child/attachment"
        params = {"limit": 200}
        while url:
            resp = self.session.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            links_base = data.get("_links", {}).get("base", self.wiki_base)
            for result in data.get("results", []):
                attachments.append(
                    {
                        "id": result["id"],
                        "filename": result["title"],
                        "media_type": result.get("extensions", {}).get("mediaType"),
                        "download_url": links_base + result["_links"]["download"],
                    }
                )
            next_link = data.get("_links", {}).get("next")
            url = (links_base + next_link) if next_link else None
            params = None
        return attachments

    def download_attachment(self, attachment):
        resp = self.session.get(attachment["download_url"])
        resp.raise_for_status()
        return resp.content
