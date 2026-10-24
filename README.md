# DSTool — Dataset Masking and Captioning Tool created for use with [OneTrainer](https://github.com/Nerogar/OneTrainer).

By [Peter Sdobnov](https://github.com/Zueuk)

## Overview

DSTool provides a visual interface for:

- Browsing and managing image datasets
- Creating and editing segmentation masks
- Generating captions using a VLM via an OpenAI-compatible API, including bulk caption generation

The tool follows the file conventions used by OneTrainer:

- `{base}.png` — image (also .jpg, .jpeg, .bmp, .tiff, .tif, .webp)
- `{base}-masklabel.png` — mask label
- `{base}.txt` — caption text

## Installation

DSTool is designed to be copied into your OneTrainer directory and run directly. No separate installation is required.

### Requirements

- Python 3.10 or later
- PySide6 and tomlkit (if not using OneTrainer's environment)
- For caption generation: a VLM server with an OpenAI-compatible chat completions API, default `http://127.0.0.1:9931`

If you prefer a standalone environment, install the required packages manually:

```bash
pip install PySide6 tomlkit
```

## Usage

Launch with the `dstool.bat` script (Windows) or run directly:

```bash
python dstool_main.py
```

Optionally, specify a dataset folder as a command-line argument:

```bash
dstool /path/to/dataset
```

### Configuration

Dataset-specific configuration is stored in a `.ds.toml` file within the dataset directory. The tool reads and writes this file automatically.

### VLM Server

DSTool communicates with a VLM server for caption generation using an OpenAI-compatible chat completions endpoint. The default configuration assumes:

- URL: `http://127.0.0.1:9931`
- A generic image captioner prompt

Adjust these settings in the tool's configuration if your server uses different values.

## Features

- **Image browser + mask editor**: Mask editing a-la the old OneTrainer dataset tools with undo/redo
- **Caption editor**: Multi-line caption editor with "trigger" words highlighting
- **Caption generation**: Using a VLM with streaming support (installed separately)
- **Batch caption generation**: Bulk captioning with options to skip or overwrite existing captions

## Dependencies

- PySide6 (Qt6 bindings)
- tomlkit (TOML configuration)

If using OneTrainer, these are already provided.

## License

See the LICENSE file for details.
