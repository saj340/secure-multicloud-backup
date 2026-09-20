"""
Package the desktop client as a Windows program that runs without Python installed.

    python build_client.py

Result: dist/SecureBackup/SecureBackup.exe (plus its support files, in the same
folder). Copy or zip the whole SecureBackup folder to move it to another computer.

The build happens in C:\\build\\smcb because PyInstaller and PySide6 together create
very long file paths, which fail inside this deeply nested project folder (the same
260-character Windows limit that keeps the virtual environment in C:\\venvs\\smcb).
"""

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
BUILD_ROOT = Path(r"C:\build\smcb")
NAME = "SecureBackup"
DEV_CA = PROJECT / "certs" / "dev-ca.pem"

# The client is plain Python + Qt. None of the server (Django) is needed, and
# leaving Qt modules we never use out keeps the package much smaller.
EXCLUDES = [
    "django", "rest_framework", "boto3", "botocore", "moto", "uvicorn", "tkinter",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest",
]


def main() -> int:
    work = BUILD_ROOT / "work"
    dist = BUILD_ROOT / "dist"
    shutil.rmtree(BUILD_ROOT, ignore_errors=True)
    BUILD_ROOT.mkdir(parents=True)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", NAME,
        "--workpath", str(work),
        "--distpath", str(dist),
        "--specpath", str(BUILD_ROOT),
        "--paths", str(PROJECT),
    ]
    for module in EXCLUDES:
        command += ["--exclude-module", module]
    command.append(str(PROJECT / "client" / "main.py"))

    print("Building (this takes a few minutes)...")
    if subprocess.run(command, cwd=PROJECT).returncode != 0:
        print("Build failed.")
        return 1

    built = dist / NAME
    if DEV_CA.exists():
        shutil.copy2(DEV_CA, built / "dev-ca.pem")
        print("Copied the development CA certificate next to the program.")
    else:
        print("Note: certs/dev-ca.pem not found; run `python -m config.devcerts` first "
              "if this build will talk to a local development server.")

    target = PROJECT / "dist" / NAME
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(exist_ok=True)
    shutil.copytree(built, target)
    print(f"Done: {target / (NAME + '.exe')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
