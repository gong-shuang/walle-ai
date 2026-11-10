import sys
from pathlib import Path

# 让测试直接 import 上一级的 main.py（无需安装包）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
