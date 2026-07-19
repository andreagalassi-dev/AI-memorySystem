import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.graph.pipeline import answer
from src.serving.session_deps import get_deps, get_serving_health

async def main():
    print("Prima:", get_serving_health())
    role = "tecnico"
    deps = get_deps(role)
    print("Dopo get_deps:", get_serving_health())
    res = await asyncio.to_thread(answer, "codice GM41013", role, deps=deps)
    print("Dopo answer:", get_serving_health())

if __name__ == "__main__":
    asyncio.run(main())
