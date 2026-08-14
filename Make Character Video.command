#!/bin/bash
# Double-click this file to build the video.
# Your channel character standing over a looping stock/backdrop clip.
# It works on the folder it is sitting in.

cd "$(dirname "$0")" || exit 1

echo
echo "  ────────────────────────────────────────────"
echo "   Character Video"
echo "  ────────────────────────────────────────────"
echo

# ffmpeg does the actual work and isn't part of macOS.
if ! command -v ffmpeg >/dev/null 2>&1; then
  # Homebrew installs here but a double-clicked script doesn't always inherit PATH.
  for dir in /opt/homebrew/bin /usr/local/bin; do
    [ -x "$dir/ffmpeg" ] && export PATH="$dir:$PATH"
  done
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "  ffmpeg isn't installed — it's the tool that builds the video."
  echo
  echo "  To install it, open Terminal and paste this:"
  echo
  echo "      /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
  echo "      brew install ffmpeg"
  echo
  echo "  Then double-click this file again."
  echo
  read -r -p "  Press Return to close. "
  exit 1
fi

python3 tools/character/make_character_video.py "$(pwd)"
status=$?

echo
if [ $status -eq 0 ]; then
  echo "  Opening the folder…"
  open .
else
  echo "  Nothing was created. The message above says why."
fi
echo
read -r -p "  Press Return to close. "
