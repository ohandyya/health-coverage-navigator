"""Write the OpenAPI schema to a file without starting a server.

`make types` feeds this to `openapi-typescript`, which is the mechanism that turns a Pydantic
change into a TypeScript compile error instead of a runtime surprise (docs/frontend_plan.md §4.3).
It is described there as the single highest-leverage choice in that document, and this module is
the half of it that lives in Python.

Dumping statically rather than curling a live `/openapi.json` is what lets `make types` work with
no `uvicorn` running — and, less obviously, on a clone where `frontend/dist` has never existed,
which is why `app.py` degrades gracefully instead of raising when the build is missing.

Output is `indent=2, sort_keys=True`, so the dump is byte-stable: a diff in the generated
`schema.d.ts` is then a real contract change rather than dict ordering.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def build_schema() -> dict[str, Any]:
    from health_coverage_navigator.api.app import create_app

    return create_app().openapi()


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump the FastAPI OpenAPI schema.")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write here instead of stdout",
    )
    args = parser.parse_args()

    text = render(build_schema())
    if args.out is not None:
        # `--out` rather than a shell `>` redirect: the shell truncates the target *before* Python
        # runs, so a failing dump would hand `openapi-typescript` a zero-byte file and produce a
        # confusing parse error instead of the actual traceback.
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out} ({len(text):,} bytes)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
