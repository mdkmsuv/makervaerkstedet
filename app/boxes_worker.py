"""One-shot worker for the transient Boxes.py HTTP endpoint."""

from __future__ import annotations

import json
import sys

from app.boxes_generator import generate_box_svg


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read(2_000_000))
        svg, details = generate_box_svg(payload)
        sys.stdout.write(json.dumps({"svg": svg, "details": details}, ensure_ascii=False))
        return 0
    except Exception as error:  # the parent returns a short, safe message
        sys.stderr.write(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
