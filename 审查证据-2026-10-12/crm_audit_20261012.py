import os
import sys
import subprocess
from pathlib import Path
from sqlalchemy.engine import make_url

ROOT = Path('/Users/zhaorongkai/Desktop/CRM-Sales-Agent-V1.1')
BACKEND = ROOT / 'backend'
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))
from app.core.config import settings

DB = 'crm_check_test_audit_20261012'
PORT = '8069'
env = dict(os.environ)
env.update(DATABASE_URL=make_url(settings.database_url).set(database=DB).render_as_string(hide_password=False),
           API_BASE=f'http://127.0.0.1:{PORT}/api/v1', PYTHONPATH=str(BACKEND),
           DEBUG='true', CI='true', SCHEDULER_ENABLED='false', DINGTALK_PUSH_OFF='1',
           WECOM_PUSH_OFF='1', WECOM_TRANSFER_ENABLED='false', DEEPSEEK_API_KEY='',
           DINGTALK_APP_KEY='', DINGTALK_APP_SECRET='', WECOM_CORP_ID='',
           ERP_PROVIDER='', ERP_APP_KEY='', ERP_APP_SECRET='',
           JWT_SECRET='isolated-audit-test-secret-20261012-local',
           FILE_ROOT='/tmp/crm_audit_20261012_files')
env.pop('DB_NAME', None)
PYTHON = str(BACKEND / '.venv/bin/python')
mode = sys.argv[1]
if mode == 'init':
    owner = make_url(env['DATABASE_URL']).username
    if owner not in {'crm', 'postgres'}:
        raise SystemExit('Unexpected local database role')
    subprocess.run(['/opt/homebrew/opt/postgresql@15/bin/psql', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'postgres', '-w', '-v', 'ON_ERROR_STOP=1', '-c', f'CREATE DATABASE {DB} OWNER {owner}'], check=True)
    subprocess.run([PYTHON, '-m', 'alembic', 'upgrade', 'head'], env=env, check=True)
    subprocess.run([PYTHON, 'scripts/seed.py'], env=env, check=True)
elif mode == 'serve':
    os.execve(PYTHON, [PYTHON, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', PORT], env)
elif mode == 'checks':
    raise SystemExit(subprocess.run(['bash', str(ROOT / 'ops/run_checks.sh')], env=env).returncode)
elif mode == 'run':
    raise SystemExit(subprocess.run([PYTHON, *sys.argv[2:]], env=env).returncode)
elif mode == 'ui':
    env.update(APP_BASE='http://127.0.0.1:5289', API_BASE=f'http://127.0.0.1:{PORT}',
               SMOKE_ENABLE_FIXTURES='1', SMOKE_OUT='/tmp/crm_audit_ui')
    os.chdir(ROOT)
    raise SystemExit(subprocess.run(['node', str(ROOT / 'ops/smoke_ui.mjs')], env=env).returncode)
elif mode == 'cleanup':
    subprocess.run(['/opt/homebrew/opt/postgresql@15/bin/psql', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'postgres', '-w', '-v', 'ON_ERROR_STOP=1', '-c', f'DROP DATABASE {DB} WITH (FORCE)'], check=True)
