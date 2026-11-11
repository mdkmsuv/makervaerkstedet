"""Source checkout facade; installed packages use python -m app.demo_workshop."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.demo_workshop import main

if __name__ == '__main__':
    raise SystemExit(main(['setup', *sys.argv[1:]]))
