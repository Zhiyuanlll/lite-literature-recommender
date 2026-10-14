#!/usr/bin/env python3
"""Preview/install an optional current-user startup service; never runs by default."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

NAME = "lite-recommender.service"


def quote_unit(value: str, command: bool = False) -> str:
    if any(char in value for char in "\n\r\x00"):
        raise ValueError("Paths must not contain control characters")
    # systemd applies specifiers even inside quoted arguments.
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--port", type=int, default=8765)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--install", action="store_true")
    action.add_argument("--remove", action="store_true")
    action.add_argument("--print", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be 1..65535")
    project = Path(__file__).resolve().parents[1]
    workspace = (args.workspace or project / "workspace").expanduser().resolve()
    python = args.python.expanduser().resolve()
    unit = "\n".join([
        "[Unit]", "Description=Lite Recommender local literature service",
        "After=network.target", "", "[Service]", "Type=simple",
        f"WorkingDirectory={quote_unit(str(project))}",
        "Environment=PYTHONUNBUFFERED=1",
        "ExecStart=" + " ".join(quote_unit(part, command=True) for part in [
            str(python), "-m", "lite_recommender", "--workspace", str(workspace),
            "serve", "--port", str(args.port),
        ]),
        "Restart=on-failure", "RestartSec=10", "", "[Install]",
        "WantedBy=default.target", "",
    ])
    if not args.install and not args.remove:
        print(unit)
        return 0
    if not sys.platform.startswith("linux") or shutil.which("systemctl") is None:
        parser.error("Installation requires Linux with systemd user services")
    destination = Path.home() / ".config" / "systemd" / "user" / NAME
    if args.remove:
        subprocess.run(["systemctl", "--user", "disable", "--now", NAME], check=True)
        if destination.exists():
            destination.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        print(f"Removed {destination}")
        return 0
    if not python.is_file():
        parser.error("--python must refer to an existing Python executable")
    # Initialize before starting the service. No synthetic data is added.
    subprocess.run([str(python), "-m", "lite_recommender", "--workspace", str(workspace), "init"], cwd=project, check=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_text(encoding="utf-8") != unit:
        parser.error(f"{destination} already exists with different contents; review it first")
    destination.write_text(unit, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", NAME], check=True)
    print(f"Installed {destination}; scheduling remains controlled by settings.json / the local UI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
