import os
import sys

# 让测试直接 import walle-agent/src/car_agent.py，不依赖安装
SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)
