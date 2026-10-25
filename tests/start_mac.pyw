"""Open the bank tool beside this file using Python Launcher on macOS."""
import os
from pathlib import Path
import sys

APP_DIR = Path(__file__).resolve().parent
os.chdir(APP_DIR)
sys.path.insert(0, str(APP_DIR))
sys.dont_write_bytecode = True

from bank_merge import gui

if __name__ == '__main__':
    gui()
