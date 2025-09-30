from importlib import resources
from typing import NamedTuple


class ReleaseEntry(NamedTuple):
    description: str
    entries: list[str]


def _read_changelog_from_package(pkg: str) -> dict[str, ReleaseEntry]:
    """Try to read CHANGELOG.md that is packaged alongside the kimi_cli module.

    This works if CHANGELOG.md is placed under the kimi_cli package directory
    (e.g., via a symlink at build-time) and included by the build backend.
    """
    package_root = resources.files(pkg)
    changelog_path = package_root / "CHANGELOG.md"
    assert changelog_path.is_file(), "CHANGELOG.md not found"
    md = changelog_path.read_text(encoding="utf-8")
    return parse_changelog(md)


def parse_changelog(md_text: str) -> dict[str, ReleaseEntry]:
    """Parse a subset of Keep a Changelog-style markdown into a map:
    version -> (description, entries)

    Parsing rules:
    - Versions are denoted by level-2 headings starting with '## ['
      Example: `## [v0.10.1] - 2025-09-18` or `## [Unreleased]`
    - For each version section, description is the first contiguous block of
      non-empty lines that do not start with '-' or '#'.
    - Entries are all markdown list items starting with '- ' under that version
      (across any subheadings like '### Added').
    """
    lines = md_text.splitlines()
    result: dict[str, ReleaseEntry] = {}

    current_ver: str | None = None
    collecting_desc = False
    desc_lines: list[str] = []
    bullet_lines: list[str] = []
    seen_content_after_header = False

    def commit():
        nonlocal current_ver, desc_lines, bullet_lines, result
        if current_ver is None:
            return
        description = "\n".join([line.strip() for line in desc_lines]).strip()
        # Deduplicate and normalize entries
        norm_entries = [
            line.strip()[2:].strip() for line in bullet_lines if line.strip().startswith("- ")
        ]
        result[current_ver] = ReleaseEntry(description=description, entries=norm_entries)

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("## ["):
            # New version section, flush previous
            commit()
            # Extract version token inside brackets
            end = line.find("]")
            ver = line[4:end] if end != -1 else line[3:].strip()
            current_ver = ver.strip()
            desc_lines = []
            bullet_lines = []
            collecting_desc = True
            seen_content_after_header = False
            continue

        if current_ver is None:
            # Skip until first version section
            continue

        if not line.strip():
            # blank line ends initial description block only after we've seen content
            if collecting_desc and seen_content_after_header:
                collecting_desc = False
            continue

        seen_content_after_header = True

        if line.lstrip().startswith("### "):
            collecting_desc = False
            continue

        if line.lstrip().startswith("- "):
            collecting_desc = False
            bullet_lines.append(line.strip())
            continue

        if collecting_desc:
            # Accumulate description until a blank line or bullets/subheadings
            desc_lines.append(line.strip())
        # else: ignore any other free-form text after description block

    # Final flush
    commit()
    return result


def format_release_notes(changelog: dict[str, ReleaseEntry]) -> str:
    # Prefer Unreleased, then latest semver-ish key
    order: list[str] = []
    if "Unreleased" in changelog:
        order.append("Unreleased")
    # Add the rest in reverse lexical order (simple heuristic)
    rest = [k for k in changelog if k != "Unreleased"]
    rest.sort(reverse=True)
    order.extend(rest)

    parts: list[str] = []
    for ver in order:
        entry = changelog[ver]
        s = f"[bold]{ver}[/bold]"
        if entry.description:
            s += f": {entry.description}"
        if entry.entries:
            for it in entry.entries:
                s += "\n[markdown.item.bullet] • [/]" + it
        parts.append(s + "\n")
    return "\n".join(parts).strip()


CHANGELOG = _read_changelog_from_package("kimi_cli")
