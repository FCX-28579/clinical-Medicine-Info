from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUPPLEMENTAL_SCRIPT_DIR = ROOT.parent / "supplemental-drug-report" / "scripts"
sys.path.insert(0, str(SUPPLEMENTAL_SCRIPT_DIR))

from supplemental_drug_report import main  # noqa: E402


if __name__ == "__main__":
    main()
