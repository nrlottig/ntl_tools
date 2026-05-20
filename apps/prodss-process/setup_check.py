"""
setup_check.py
Checks and installs required packages for YSIAnalyzer.py.
Run this once before launching the app:  python setup_check.py
"""

import subprocess
import sys


REQUIRED = [
    "streamlit",
    "pandas",
    "numpy",
]


def check_tkinter():
    """tkinter ships with Python but is missing on some Linux installs."""
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False


def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])


def main():
    print("=" * 50)
    print("YSIAnalyzer — dependency check")
    print("=" * 50)

    all_ok = True

    # Check pip-installable packages
    for pkg in REQUIRED:
        try:
            __import__(pkg)
            print(f"  ✅  {pkg}")
        except ImportError:
            print(f"  ⚠️  {pkg} not found — installing...")
            try:
                install(pkg)
                print(f"  ✅  {pkg} installed successfully")
            except Exception as e:
                print(f"  ❌  Failed to install {pkg}: {e}")
                all_ok = False

    # Check tkinter separately (not pip-installable)
    if check_tkinter():
        print("  ✅  tkinter")
    else:
        all_ok = False
        print("  ❌  tkinter not found")
        print()
        print("     tkinter comes bundled with Python but is missing on your system.")
        print("     Fix depends on your OS:")
        print("       macOS:   reinstall Python from python.org (includes tkinter)")
        print("       Ubuntu:  sudo apt-get install python3-tk")
        print("       Windows: reinstall Python from python.org and check")
        print("                'tcl/tk and IDLE' in the optional features list")

    print()
    if all_ok:
        print("All dependencies satisfied. Launch the app with:")
        print()
        print("  streamlit run YSIAnalyzer.py")
    else:
        print("Fix the issues above, then re-run this script to verify.")

    print("=" * 50)


if __name__ == "__main__":
    main()
