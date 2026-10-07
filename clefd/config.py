"""Paths, .env secrets and hot-reloaded TOML config files."""
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
WEB_DIR = ROOT / "web"
DATA_DIR = Path(os.environ.get("CLEF_DATA", Path.home() / ".local/share/clef"))
DB_PATH = DATA_DIR / "clef.db"

LLAMA_URL = os.environ.get("CLEF_LLAMA_URL", "http://127.0.0.1:8090")
HOST = os.environ.get("CLEF_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLEF_PORT", "8077"))


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal KEY=VALUE loader so secrets stay out of the repo. Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class TomlFile:
    """A config file that re-reads itself when its mtime changes."""

    def __init__(self, name: str):
        self.path = CONFIG_DIR / name
        self._mtime = 0.0
        self._data: dict = {}

    def get(self) -> dict:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return self._data
        if mtime != self._mtime:
            try:
                self._data = tomllib.loads(self.path.read_text())
                self._mtime = mtime
            except tomllib.TOMLDecodeError as e:
                # Keep the last good config while the file is mid-edit.
                print(f"[config] {self.path.name}: {e}", flush=True)
        return self._data


sources = TomlFile("sources.toml")
mail = TomlFile("mail.toml")
jobs = TomlFile("jobs.toml")
