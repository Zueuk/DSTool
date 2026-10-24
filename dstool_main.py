"""
Dataset Masking and Captioning Tool
by Peter Sdobnov https://github.com/Zueuk
created for use with OneTrainer
Usage: python dstool_main.py [/path/to/folder]
"""

import os
import sys
import argparse
import traceback

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from dstool.main_window import MainWindow


def main():
    QApplication.setStyle("Fusion")

    app = QApplication(sys.argv)

    def handle_uncaught_exception(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(msg, file=sys.stderr)

    sys.excepthook = handle_uncaught_exception

    # Parse command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", nargs="?", default="", help="Folder path to open")
    parser.add_argument("--hide", action="store_true", help="Start in 🙈 mode")
    args = parser.parse_args()

    folder = args.folder if args.folder and os.path.isdir(args.folder) else ""

    window = MainWindow(folder, hide_image_mode=args.hide)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()