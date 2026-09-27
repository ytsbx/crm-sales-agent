# 保证 `cd backend && python -m pytest` 与任意工作目录下都能 import 到 app 包。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
