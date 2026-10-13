"""
Root conftest — ensures Member2/ is on sys.path so that
`from backend.services.X import Y` works when pytest is run from Member2/.
"""
import sys
from pathlib import Path

# Member2/ directory
sys.path.insert(0, str(Path(__file__).parent))
