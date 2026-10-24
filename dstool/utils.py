import re
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QFileInfo, QDir


SUPPORTED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.webp'}
MASK_SUFFIX = "-masklabel"

# Portrait aspect ratios from OneTrainer's MGDS AspectBucketing.all_possible_input_aspects
_ASPECT_RATIOS = [
    (4, 5), (2, 3), (4, 7), (1, 2), (2, 5), (1, 3), (2, 7), (1, 4),
]


def closest_aspect_ratio(w: int, h: int) -> str:
    if w <= 0 or h <= 0:
        return ""
    if w == h:
        return "1:1"
    short, long = (w, h) if w < h else (h, w)
    image_ratio = short / long
    a, b = min(
        _ASPECT_RATIOS,
        key=lambda ar: abs(image_ratio - ar[0] / ar[1])
    )
    if w > h:
        a, b = b, a
    return f"{a}:{b}"


def is_image_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def is_mask_file(filename: str) -> bool:
    p = Path(filename)
    return p.stem.endswith(MASK_SUFFIX) and p.suffix.lower() in SUPPORTED_EXTENSIONS


def mask_filename(base: str) -> str:
    return f"{base}{MASK_SUFFIX}.png"


def caption_filename(base: str) -> str:
    return f"{base}.txt"


def base_from_mask(mask_filename: str) -> str:
    p = Path(mask_filename)
    return p.stem[:-len(MASK_SUFFIX)]


def words_to_regex_patterns(words: str) -> list:
    """Convert wildcard words to compiled regex patterns. * and ? are supported."""
    patterns = []
    for word in words.split():
        try:
            pattern_str = ''.join(
                r'\w*' if c == '*' else r'\w' if c == '?' else re.escape(c)
                for c in word
            )
            patterns.append(re.compile(r'\b' + pattern_str + r'\b', re.IGNORECASE))
        except re.error:
            pass
    return patterns


def regex_matches_any(patterns: list, text: str) -> bool:
    return any(p.search(text) for p in patterns)


def show_in_file_explorer(file_path: str) -> bool:
    """Reveal and select a file in the native file explorer (Windows, macOS, Linux)."""
    file_info = QFileInfo(file_path)
    if not file_info.exists():
        return False

    canonical_path = file_info.canonicalFilePath()

    if sys.platform == 'win32':
        # Windows: Use explorer.exe with `/select,`
        params: list[str] = []
        if not file_info.isDir():
            params.append("/select,")
        params.append(QDir.toNativeSeparators(canonical_path))
        QProcess.startDetached("explorer.exe", params)

    elif sys.platform == 'darwin':
        # macOS: Use AppleScript to tell Finder
        script_args = [
            "-e",
            f'tell application "Finder" to reveal POSIX file "{canonical_path}"',
            "-e",
            'tell application "Finder" to activate',
        ]
        QProcess.execute("/usr/bin/osascript", script_args)

    else:
        # Linux: Open the parent directory (file selection is unreliable across file managers)
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(file_info.absolutePath()))

    return True
