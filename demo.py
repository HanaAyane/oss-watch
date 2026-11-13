"""无需安装依赖即可运行第一期演示。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from oss_watch.cli import main

if __name__ == "__main__":
    main()
