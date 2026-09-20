"""The hosted process must not import the local game or audio runtime."""
import os
from pathlib import Path
import subprocess
import sys


def test_gateway_runs_without_local_mapping_dependencies(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = '''
import importlib.abc
import sys
from pathlib import Path

class BlockLocalRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = {'numpy', 'scipy', 'torch', 'librosa', 'soundfile', 'UnityPy', 'ortools',
                   'beatforge.api', 'beatforge.premium', 'beatforge.preview', 'beatforge.learning'}
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise ImportError('Gateway imported local runtime: ' + fullname)

sys.meta_path.insert(0, BlockLocalRuntime())
from fastapi.testclient import TestClient
from beatforge.connector import create_app
from beatforge.connector_auth import Principal
app = create_app(database=Path(sys.argv[1]), credentials={'a' * 32: Principal('test', frozenset({'read'}))})
with TestClient(app, base_url='http://localhost') as client:
    assert client.get('/health').status_code == 200
    assert client.get('/api/jobs', headers={'Authorization': 'Bearer ' + 'a' * 32}).status_code == 200
    assert client.get('/api/jobs').status_code == 401
'''
    env = {**os.environ, 'PYTHONPATH': str(root / 'src')}
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path / 'jobs.sqlite3')],
                            cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
