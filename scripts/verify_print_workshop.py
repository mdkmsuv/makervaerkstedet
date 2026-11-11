"""Isolated demo UI audit; never constructs physical printer/camera drivers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.server import Application, Handler, Server
from scripts.smoke_slice import cube

with tempfile.TemporaryDirectory(prefix='elevhub-print-audit-') as folder:
    server = Server(('127.0.0.1', 0), Handler)
    server.app = Application(folder, demo=True)
    # Keep accepted jobs in the demo queue for deterministic UI assertions.
    server.app.worker.stop()
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    stl = Path(folder) / 'audit-terning.stl'
    stl.write_bytes(cube())
    env = dict(os.environ, PRINT_AUDIT_BASE=f'http://127.0.0.1:{server.server_port}',
               PRINT_AUDIT_TOKEN=server.app.token, PRINT_AUDIT_STL=str(stl))
    try:
        result = subprocess.run(['node', str(ROOT/'scripts/verify_print_workshop.cjs')], env=env, cwd=ROOT, timeout=120)
    finally:
        server.shutdown()
        runner.join()
        server.app.shutdown()
        server.app.queue.close()
        server.server_close()
    sys.exit(result.returncode)
