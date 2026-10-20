"""Check documentation links locally or against the files selected for Git."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracked", action="store_true",
                        help="check only Git-index files and require link targets in the index")
    args = parser.parse_args(argv)
    tracked = None
    if args.tracked:
        try:
            names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"cannot read Git index: {error}")
            return 1
        tracked = {name for name in names if name}
        documents = [ROOT / name for name in sorted(tracked) if name.endswith(".md")]
        workspaces = [ROOT / name for name in sorted(tracked) if name.endswith(".code-workspace")]
    else:
        documents = [ROOT / "README.md", ROOT / "CONTRIBUTING.md"]
        for folder in ("docs", "src", "ini", "data", "tools"):
            documents.extend((ROOT / folder).rglob("*.md"))
        workspaces = [path for path in (
            ROOT / "Digital-NMC.code-workspace",
            ROOT / "docs/papers/PIM-NMC-Literature.code-workspace",
        ) if path.exists()]

    problems = []
    checked_links = 0
    for path in documents:
        if not path.exists():
            problems.append(f"missing document: {path.relative_to(ROOT)}")
            continue
        content = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
        for target in re.findall(r"!?\[[^\]\n]*\]\(([^)\n]+)\)", content):
            raw = target.strip("<>")
            if re.match(r"^[a-zA-Z]+:", raw) or raw.startswith("#"):
                continue
            file_part = raw.partition("#")[0]
            checked_links += 1
            resolved = (path.parent / unquote(file_part)).resolve()
            if not resolved.exists():
                problems.append(f"{path.relative_to(ROOT)} -> {target}")
            elif tracked is not None:
                try:
                    relative = resolved.relative_to(ROOT).as_posix()
                except ValueError:
                    problems.append(f"{path.relative_to(ROOT)} -> nonportable target {target}")
                    continue
                if relative not in tracked:
                    problems.append(f"{path.relative_to(ROOT)} -> target not in Git index: {target}")
    for workspace in workspaces:
        try:
            config = json.loads(workspace.read_text(encoding="utf-8"))
            for entry in config["folders"]:
                if not (workspace.parent / entry["path"]).is_dir():
                    problems.append(f'{workspace.name} -> {entry["path"]}')
        except (OSError, ValueError, KeyError) as error:
            problems.append(f"{workspace.name}: {error}")
    for old in ("software", "hardware", "validation", "spec", "papers", "open_source_references"):
        if (ROOT / old).exists():
            problems.append(f"obsolete top-level wrapper still exists: {old}")
    if problems:
        print("\n".join(problems))
        return 1
    scope = "Git index" if tracked is not None else "local"
    print(f"Layout check: PASS ({checked_links} {scope} links; {len(workspaces)} workspaces; no obsolete wrappers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
