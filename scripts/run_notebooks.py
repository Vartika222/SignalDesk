"""Execute every notebooks/0*.py (jupytext percent format) into reports/<name>.ipynb."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    for nb in sorted((ROOT / "notebooks").glob("0*.py")):
        target = out_dir / f"{nb.stem}.ipynb"
        print(f"executing {nb.name} -> {target.relative_to(ROOT)}", flush=True)
        rc = subprocess.call(
            ["jupytext", "--to", "ipynb", "--execute", "--output", str(target), str(nb)], cwd=ROOT
        )
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
