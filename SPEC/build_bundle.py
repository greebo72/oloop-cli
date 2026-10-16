"""Assemble one upload file for an AI app builder from the spec material.

Usage: python SPEC/build_bundle.py [output]   (default: SPEC/dist/oloop-app-bundle.md)

The bundle holds the client brief, the relevant spec sections, both schemas and every
fixture file. Nothing is written into the repository except the output file.
"""

import re
import sys
from pathlib import Path

SPEC = Path(__file__).resolve().parent
KEEP = ("Purpose", "Non-goals", "Backup/recovery", "File format")


def sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## ", text, flags=re.M)[1:]
    return {p.split("\n", 1)[0].split(" (")[0].strip(): "## " + p.rstrip() for p in parts}


def fence(path: Path, lang: str) -> str:
    rel = path.relative_to(SPEC).as_posix()
    return f"#### `{rel}`\n\n```{lang}\n{path.read_text(encoding='utf-8').rstrip()}\n```\n"


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else SPEC / "dist" / "oloop-app-bundle.md"
    spec = sections((SPEC / "SPEC.md").read_text(encoding="utf-8"))
    chunks = [(SPEC / "app-brief.md").read_text(encoding="utf-8").rstrip()]

    chunks.append("# Included spec sections\n\nThese are excerpts from `SPEC.md`. They mention a CLI "
                  "and a possible desktop app. This app is another client of the same files.")
    chunks += [spec[name] for name in KEEP]

    chunks.append("# JSON Schemas")
    chunks += [fence(p, "json") for p in sorted((SPEC / "schemas").glob("*.schema.json"))]

    chunks.append("# Fixtures\n\n" + (SPEC / "fixtures" / "README.md").read_text(encoding="utf-8").split("\n", 2)[2].strip())
    for kind in ("valid", "invalid"):
        chunks.append(f"## {kind.capitalize()} cases")
        for f in sorted((SPEC / "fixtures" / kind).rglob("*")):
            if f.is_file():
                chunks.append(fence(f, "json" if f.suffix == ".json" else "markdown"))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    print(f"{out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
