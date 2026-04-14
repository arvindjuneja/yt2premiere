#!/bin/bash
# Launch YouTube Downloader
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/venv/bin/activate"
python3 "$DIR/app.py"
