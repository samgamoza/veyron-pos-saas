#!/usr/bin/env python3
"""Validate url_for endpoint references against registered Flask routes."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FLASK_ENV", "testing")

REF_RE = re.compile(r"""url_for\(\s*['"]([^'"]+)['"]""")


def collect_refs() -> dict[str, list[str]]:
    refs: dict[str, list[str]] = {}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {".html", ".py", ".js"}:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(part in rel for part in (".git/", "node_modules/", "__pycache__/", ".venv/")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in REF_RE.finditer(text):
            refs.setdefault(m.group(1), []).append(rel)
    return refs


def main() -> int:
    from app.core.flask_app import create_flask_application

    app = create_flask_application()
    endpoints = set(app.view_functions.keys())
    refs = collect_refs()
    missing = sorted(k for k in refs if k not in endpoints)
    print(f"Referenced endpoints: {len(refs)}")
    print(f"Registered endpoints: {len(endpoints)}")
    if missing:
        print("\nMISSING ENDPOINTS:")
        for e in missing:
            locs = ", ".join(sorted(set(refs[e]))[:5])
            print(f"  {e}  <- {locs}")
        return 1
    print("\nAll url_for references resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
