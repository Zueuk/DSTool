"""Folder settings for DSTool stored in TOML format."""

import os
import tomlkit
from tomlkit.exceptions import ParseError, TOMLKitError
from typing import IO

from PySide6.QtCore import qWarning


FOLDER_CONFIG_FILENAME = ".ds.toml"

DEFAULT_SERVER_URL = "http://127.0.0.1:9931"
DEFAULT_REQUEST_TIMEOUT = 180
DEFAULT_CAPTIONER_PROMPT = (
    "Write a single, cohesive paragraph in natural English that describes "
    "the provided image in enough detail that it can be used as a prompt. "
    "Use complete sentences; no bullet points, lists, or comma-separated tags."
)


def _open_config(path: str, mode: str = "r") -> IO[str]:
    f = open(path, mode, encoding="utf-8")
    if "r" not in mode:
        return f

    # Peek to check for valid UTF-8
    try:
        f.read(1)
        # Seek back to start
        f.seek(0)
        return f
    except UnicodeDecodeError:
        f.close()
        # Fall back to Windows-1252
        return open(path, mode, encoding="cp1252")


def _config_path(folder: str) -> str:
    return os.path.join(folder, FOLDER_CONFIG_FILENAME)


def load_config(folder: str) -> dict:
    config_path = _config_path(folder)
    if not os.path.isfile(config_path):
        parent_folder = os.path.dirname(folder.rstrip('/\\'))
        if parent_folder and parent_folder != folder:
            config_path = _config_path(parent_folder)

    result = {
        "trigger_words": "",
        "hide_mode": False,
        "captioner_prompt": "",
    }

    if os.path.isfile(config_path):
        try:
            with _open_config(config_path, "r") as f:
                data = tomlkit.load(f)

            result["trigger_words"] = data.get("trigger_words", "")
            result["hide_mode"] = bool(data.get("hide_mode", False))
            result["captioner_prompt"] = data.get("captioner_prompt", "")
        except (OSError, ParseError) as e:
            qWarning(f"Failed to load config: {e}")

    return result


def save_config(
    folder: str,
    trigger_words: str,
    captioner_prompt: str,
    hide_mode: bool = False,
) -> bool:
    config_path = _config_path(folder)
    try:
        if os.path.isfile(config_path):
            with _open_config(config_path, "r") as f:
                doc = tomlkit.load(f)
        else:
            doc = tomlkit.document()

        doc["trigger_words"] = trigger_words
        if hide_mode:
            doc["hide_mode"] = True
        elif "hide_mode" in doc:
            del doc["hide_mode"]
        doc["captioner_prompt"] = tomlkit.string(captioner_prompt, multiline=True)

        with open(config_path, "w", encoding="utf-8") as f:
            tomlkit.dump(doc, f)

        return True
    except (OSError, TOMLKitError) as e:
        qWarning(f"Failed to save config: {e}")
        return False
