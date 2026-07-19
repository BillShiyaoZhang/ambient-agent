"""Verify the paired Chinese/English Docsify source tree and internal links."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_ROOT = REPO_ROOT / "docs"
EN_ROOT = DOCS_ROOT / "en"
IGNORED_PARTS = {"node_modules", ".vitepress"}
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+", re.MULTILINE)


def markdown_files(root: Path) -> set[str]:
    result: set[str] = set()
    for path in root.rglob("*.md"):
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        result.add(relative.as_posix())
    return result


def resolve_internal_link(source: Path, link: str) -> Path | None:
    target = link.split("#", 1)[0].split("?", 1)[0]
    if not target or target.startswith(("http://", "https://", "mailto:")):
        return None
    if target.startswith("file://"):
        return Path(target)
    if target.startswith("/"):
        path = DOCS_ROOT / target.lstrip("/")
    else:
        path = source.parent / target
    if target.endswith("/"):
        path = path / "index.md"
    elif path.suffix == "":
        path = path.with_suffix(".md")
    return path.resolve()


def main() -> int:
    errors: list[str] = []
    all_docs = markdown_files(DOCS_ROOT)
    chinese = {path for path in all_docs if not path.startswith("en/")}
    english = markdown_files(EN_ROOT)

    for path in sorted(chinese - english):
        errors.append(f"Missing English pair: docs/en/{path}")
    for path in sorted(english - chinese):
        errors.append(f"Missing Chinese pair: docs/{path}")

    for relative in sorted(chinese & english):
        zh_path = DOCS_ROOT / relative
        en_path = EN_ROOT / relative
        zh_levels = [len(value) for value in HEADING_RE.findall(zh_path.read_text(encoding="utf-8"))]
        en_levels = [len(value) for value in HEADING_RE.findall(en_path.read_text(encoding="utf-8"))]
        if zh_levels != en_levels:
            errors.append(
                f"Heading structure differs: docs/{relative} {zh_levels} != docs/en/{relative} {en_levels}"
            )

    source_paths = [DOCS_ROOT / path for path in sorted(chinese)] + [EN_ROOT / path for path in sorted(english)]
    for source in source_paths:
        content = source.read_text(encoding="utf-8")
        if "file://" in content:
            errors.append(f"Local file URL is not portable: {source.relative_to(REPO_ROOT)}")
        for raw_link in LINK_RE.findall(content):
            link = raw_link.strip().strip("<>")
            target = resolve_internal_link(source, link)
            if target is None or link.startswith("file://"):
                continue
            try:
                target.relative_to(DOCS_ROOT.resolve())
            except ValueError:
                errors.append(f"Link escapes docs/: {source.relative_to(REPO_ROOT)} -> {link}")
                continue
            if not target.exists():
                errors.append(f"Broken link: {source.relative_to(REPO_ROOT)} -> {link}")

    zh_nav = (DOCS_ROOT / "_navbar.md").read_text(encoding="utf-8")
    en_nav = (EN_ROOT / "_navbar.md").read_text(encoding="utf-8")
    if LINK_RE.findall(zh_nav) != ["/en/"]:
        errors.append("Chinese navbar must contain exactly one language switch to /en/")
    if LINK_RE.findall(en_nav) != ["/"]:
        errors.append("English navbar must contain exactly one language switch to /")

    index_html = (DOCS_ROOT / "index.html").read_text(encoding="utf-8")
    expected_config = (
        "homepage: 'index.md'",
        "'/en/': '_coverpage.md'",
        "'/(?!en(?:/|$)).*/_sidebar.md': '/_sidebar.md'",
        "'/(?!en(?:/|$)).*/_navbar.md': '/_navbar.md'",
        "'/en/': '/en/index.md'",
    )
    for config_line in expected_config:
        if config_line not in index_html:
            errors.append(f"Missing language-routing config in docs/index.html: {config_line}")
    if "'/en/': '/en/guide/" in index_html or "'/': '/guide/" in index_html:
        errors.append("Language home routes must not be redirected away from their index pages")

    if errors:
        print("Documentation verification failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Documentation verification succeeded: {len(chinese)} Chinese/English page pairs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
