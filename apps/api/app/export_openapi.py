"""Write the OpenAPI contract the frontend types are generated from.

    uv run python -m app.export_openapi            # write
    uv run python -m app.export_openapi --check    # fail if out of date (CI)
"""

import json
import sys
from pathlib import Path

from app.main import create_app

TARGET = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "generated" / "openapi.json"


def main() -> int:
    spec = json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != spec:
            print(f"{TARGET} is out of date: run `make contracts`", file=sys.stderr)
            return 1
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(spec, encoding="utf-8")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
