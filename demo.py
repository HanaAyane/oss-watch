"""主入口 pi 调用本机智能判断；demo 保留为免依赖的固定规则对照。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from oss_watch.cli import main

if __name__ == "__main__":
    main()
