"""Copy the authoritative schemas from spec/schemas (the oloop-spec submodule) into the package.

The wheel must be self-contained, so the package carries a copy.
`tests/test_conformance.py` fails if the copy drifts from the source.
"""

import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "spec" / "schemas"
DST = HERE / "oloop" / "schemas"

for f in sorted(SRC.glob("*.schema.json")):
    shutil.copyfile(f, DST / f.name)
    print(f"copied {f.name}")
