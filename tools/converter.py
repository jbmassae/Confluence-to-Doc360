"""Converts Confluence storage-format XHTML into Document360 WYSIWYG HTML.

Confluence storage format is XHTML plus namespaced macro elements
(ac:structured-macro, ri:attachment, etc).

Callouts reproduce Document360's own native callout markup exactly, reverse
engineered 2026-07-08 by inspecting an article where a human colored all 4
of Document360's built-in callout types (Info/Warning/Error/Success) via
the portal's own callout picker, then reading the saved HTML back via
GET /v2/Articles/{id}. Document360 has no native "Note" type, so
Confluence's `note` macro maps to the closest native box (see
CONFLUENCE_MACRO_TO_DOC360) rather than inventing an unstyled one - a class
name Document360 doesn't recognize risks not rendering at all (that's what
happened with the first attempt at this: a home-grown `callout-info` class
name with only a `background-color` inline style did not render, while
Document360's own generated markup - a different class name plus a fuller
inline style including `border-left` and `overflow` - does).

Confluence's `expand` macro (a titled, collapsible section) maps onto
Document360's native Accordion block, same reverse-engineering method as
callouts: a human added one Accordion item via the portal editor, saved,
and the real markup - a `<details>`/`<summary>` pair wrapped in a
`div.details-wrapper` plus a trailing decorative `<button>` sibling - was
read back via GET /v2/Articles/{id} (confirmed 2026-07-08). Document360
also has an "FAQ" block (multiple accordion items under one `<h3>` heading,
wrapped in `<editor360-faq>`) but a single Confluence `expand` maps more
directly onto one standalone Accordion item, not the FAQ group.

Parsing uses bs4's "html.parser": Confluence's ac:/ri: tags are plain
lowercase strings to that parser (no real namespace handling), which is
exactly the "treat as opaque tag name" behavior this module wants. CDATA
blocks (used inside code macros) are stashed via regex before parsing
since html.parser does not preserve CDATA content correctly.
"""
import html as html_lib
import re

from bs4 import BeautifulSoup

# Document360's own native callout types, exact class/color values captured
# from Document360-generated HTML (confirmed 2026-07-08). Do not tweak the
# class name or drop data-background/data-border/style - untested variations
# risk silently not rendering, same as the original guessed markup didn't.
NATIVE_CALLOUTS = {
    "info": {"class": "infoBox", "bg": "#ddf7ff", "border": "#006a8a", "title": "Info"},
    "warning": {"class": "warningBox", "bg": "#fdf2ce", "border": "#7f6416", "title": "Warning"},
    "error": {"class": "errorBox", "bg": "#f9e2e4", "border": "#7e1115", "title": "Error"},
    "success": {"class": "successBox", "bg": "#c4f2d4", "border": "#029e38", "title": "Success"},
}

# Confluence has 4 macros (info/note/warning/tip); Document360 has 4 native
# callout types but no "Note". Mapped by each macro's actual default
# Confluence color (info=blue, tip=green, note=yellow/tan, warning=red) to
# its closest Document360 native type - "note" reuses the Warning box since
# there's nothing closer available.
CONFLUENCE_MACRO_TO_DOC360 = {
    "info": "info",
    "tip": "success",
    "warning": "error",
    "note": "warning",
}


def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgb({r}, {g}, {b})"


def render_callout(doc360_type, title, inner_html):
    spec = NATIVE_CALLOUTS[doc360_type]
    bg_rgb = _hex_to_rgb(spec["bg"])
    border_rgb = _hex_to_rgb(spec["border"])
    return (
        f'<blockquote class="{spec["class"]}" data-background="{spec["bg"]}" '
        f'data-border="{spec["border"]}" style="background:{bg_rgb};'
        f'border-left:4px solid {border_rgb};overflow:auto;">'
        f'<div class="blockquote-title"><p>{html_lib.escape(title)}</p></div>'
        f"{inner_html}</blockquote>"
    )


def render_accordion(title, inner_html):
    return (
        '<div class="details-wrapper details-wrapper_rendered" data-type="details-wrapper">'
        '<details data-opened="true" data-button-align="right" '
        'data-details-border="withBorder" data-expand-icon="arrow" data-lock="false">'
        f"<summary><p>{html_lib.escape(title)}</p></summary>"
        f'<div data-type="details-content">{inner_html}</div>'
        "</details>"
        '<button class="details-arrow" type="details"></button>'
        "</div>"
    )


_CDATA_RE = re.compile(
    r"<ac:plain-text-body><!\[CDATA\[(.*?)\]\]></ac:plain-text-body>", re.DOTALL
)


def referenced_attachment_filenames(storage_html):
    """Filenames of every ri:attachment referenced in the page body (images, etc)."""
    soup = BeautifulSoup(storage_html, "html.parser")
    names = set()
    for att in soup.find_all("ri:attachment"):
        filename = att.get("ri:filename")
        if filename:
            names.add(filename)
    return names


def convert(storage_html, resolve_image_url):
    """Convert Confluence storage HTML to Document360-ready HTML.

    resolve_image_url: callable(filename) -> url string, or None if that
    attachment wasn't uploaded (a warning is recorded and a blank src used).

    Returns (html_string, warnings list).
    """
    warnings = []
    cdata_blocks = []

    def stash_cdata(match):
        idx = len(cdata_blocks)
        cdata_blocks.append(match.group(1))
        return f"<ac:plain-text-body>__CDATA_PLACEHOLDER_{idx}__</ac:plain-text-body>"

    prepared = _CDATA_RE.sub(stash_cdata, storage_html)
    soup = BeautifulSoup(prepared, "html.parser")

    def restore_cdata(text):
        match = re.fullmatch(r"__CDATA_PLACEHOLDER_(\d+)__", text.strip())
        return cdata_blocks[int(match.group(1))] if match else text

    def parse_fragment(html_str):
        return list(BeautifulSoup(html_str, "html.parser").contents)

    def replace(tag, new_html):
        fragment = parse_fragment(new_html)
        if fragment:
            tag.replace_with(*fragment)
        else:
            tag.decompose()

    def macro_param(macro, param_name):
        for param in macro.find_all("ac:parameter", recursive=False):
            if param.get("ac:name") == param_name:
                return param.get_text()
        return None

    def transform_macro(macro):
        name = macro.get("ac:name")

        if name in CONFLUENCE_MACRO_TO_DOC360:
            body = macro.find("ac:rich-text-body")
            inner = body.decode_contents() if body else ""
            doc360_type = CONFLUENCE_MACRO_TO_DOC360[name]
            title = NATIVE_CALLOUTS[doc360_type]["title"]
            replace(macro, render_callout(doc360_type, title, inner))
        elif name == "code":
            lang = macro_param(macro, "language")
            body = macro.find("ac:plain-text-body")
            code_text = restore_cdata(body.get_text()) if body else ""
            escaped = html_lib.escape(code_text)
            lang_attr = f' class="language-{lang}"' if lang else ""
            replace(macro, f"<pre><code{lang_attr}>{escaped}</code></pre>")
        elif name == "expand":
            body = macro.find("ac:rich-text-body")
            inner = body.decode_contents() if body else ""
            title = macro_param(macro, "title") or ""
            replace(macro, render_accordion(title, inner))
        elif name == "panel":
            body = macro.find("ac:rich-text-body")
            inner = body.decode_contents() if body else ""
            title = macro_param(macro, "title")
            if title:
                replace(macro, render_callout("info", title, inner))
            else:
                replace(macro, inner)
            warnings.append("Macro 'panel' converted to a generic info callout; review formatting")
        elif name == "toc":
            macro.decompose()
        else:
            body = macro.find("ac:rich-text-body") or macro.find("ac:plain-text-body")
            inner = body.decode_contents() if body else ""
            replace(macro, inner)
            warnings.append(
                f"Unrecognized macro '{name}': content kept, formatting stripped - review manually"
            )

    # Transform leaf-most macros first so nested macros (eg. code inside a note) convert cleanly.
    while True:
        macros = soup.find_all("ac:structured-macro")
        if not macros:
            break
        leaves = [m for m in macros if not m.find("ac:structured-macro")]
        for macro in leaves or macros:
            transform_macro(macro)

    for img in soup.find_all("ac:image"):
        alt = img.get("ac:alt") or img.get("ac:title") or ""
        attachment = img.find("ri:attachment")
        url_tag = img.find("ri:url")
        if attachment is not None:
            filename = attachment.get("ri:filename")
            src = resolve_image_url(filename)
            if not src:
                warnings.append(f"Image '{filename}' referenced but not uploaded - left blank")
                src = ""
            replace(img, f'<img src="{src}" alt="{alt or filename}">')
        elif url_tag is not None:
            src = url_tag.get("ri:value")
            replace(img, f'<img src="{src}" alt="{alt}">')

    for link in soup.find_all("ac:link"):
        page_ref = link.find("ri:page")
        body = link.find("ac:plain-text-link-body") or link.find("ac:link-body")
        if page_ref is not None:
            title = page_ref.get("ri:content-title") or "linked page"
            text = body.get_text() if body else title
            warnings.append(
                f"Internal Confluence link to '{title}' converted to plain text - re-link manually"
            )
            replace(link, f"<span>{text}</span>")

    for emoticon in soup.find_all("ac:emoticon"):
        emoticon.replace_with(emoticon.get("ac:emoji-fallback") or "")

    for task_list in soup.find_all("ac:task-list"):
        items = []
        for task in task_list.find_all("ac:task"):
            status = task.find("ac:task-status")
            body = task.find("ac:task-body")
            checked = bool(status) and status.get_text().strip().lower() == "complete"
            items.append(f'<li>{"☑" if checked else "☐"} {body.get_text() if body else ""}</li>')
        replace(task_list, "<ul>" + "".join(items) + "</ul>")

    for layout_tag in ("ac:layout", "ac:layout-section", "ac:layout-cell"):
        for el in soup.find_all(layout_tag):
            el.unwrap()

    # Document360 renders the article title as its own H1, so an H1 in the
    # body would duplicate that top-level heading - demote to H2.
    for h1 in soup.find_all("h1"):
        h1.name = "h2"

    leftover = set()
    for el in soup.find_all(lambda t: t.name and ":" in t.name):
        leftover.add(el.name)
        el.unwrap()
    if leftover:
        warnings.append(
            f"Stripped unhandled Confluence tags (content kept, formatting lost): {sorted(leftover)}"
        )

    return str(soup), warnings
