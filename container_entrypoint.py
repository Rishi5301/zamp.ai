"""Initialize a mounted volume, then drop privileges before serving requests."""
import os
from pathlib import Path

if os.geteuid() == 0:
    data = Path(os.environ.get('LEDGERFLOW_DATA', '/var/lib/ledgerflow'))
    data.mkdir(parents=True, exist_ok=True)
    os.chown(data, 10001, 10001)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
os.execvp('python', ['python', 'app.py'])
