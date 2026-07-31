import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_core.rag.ingest import ingest_file


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/ingest_docs.py /path/to/file")
        raise SystemExit(1)
    print(ingest_file(sys.argv[1]))
