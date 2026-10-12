import os
from app.core.config import settings, Settings
os.environ.pop('DEBUG', None)
settings.debug=False
from app.main import app
from fastapi.testclient import TestClient
print('DEBUG_DEFAULT', Settings(_env_file=None).debug)
response=TestClient(app).get('/docs')
print('CI_STARTUP_PROBE', response.status_code)
