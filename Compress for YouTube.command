#!/bin/bash
# Double-click. Compresses the most finished video in this folder.
cd "$(dirname "$0")" || exit 1
echo; echo "  ────────────────────────────────────────────"; echo "   Compress for YouTube"; echo "  ────────────────────────────────────────────"; echo
if ! command -v ffmpeg >/dev/null 2>&1; then
  for dir in /opt/homebrew/bin /usr/local/bin; do [ -x "$dir/ffmpeg" ] && export PATH="$dir:$PATH"; done
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "  ffmpeg isn't installed. In Terminal run:"; echo; echo "      brew install ffmpeg"; echo
  read -r -p "  Press Return to close. "; exit 1
fi
python3 tools/compress/compress_for_youtube.py "$(pwd)"
status=$?
echo
if [ $status -eq 0 ]; then echo "  Opening the folder…"; open .; else echo "  Nothing was created. The message above says why."; fi
echo
read -r -p "  Press Return to close. "
