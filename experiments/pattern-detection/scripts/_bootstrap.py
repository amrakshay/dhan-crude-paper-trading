"""Put the experiment root on sys.path so scripts/ can import patlib/."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
