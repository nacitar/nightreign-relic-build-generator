import subprocess
import sys
from pathlib import Path


def get_project_dir() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return None


def main() -> int:
    project_root_dir = get_project_dir()
    if project_root_dir is None:
        print("ERROR: could not determine project directory", file=sys.stderr)
        return 1
    try:
        commands = [
            ["black", "."],
            ["isort", "."],
            ["mypy", "."],
            ["flake8", "."],
            ["pytest", "."],
        ]
        for command in commands:
            print(f"Running {command[0]}...")
            subprocess.run(command, check=True, cwd=project_root_dir)
            print()
    except subprocess.CalledProcessError as e:
        return e.returncode
    print("All checks completed successfully!")
    return 0
