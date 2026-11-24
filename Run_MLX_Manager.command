#!/bin/bash
# MLX Studio — double-click launcher
# Starts the local server and opens the native window (pywebview),
# falling back to the default browser if pywebview is unavailable.

cd "$(dirname "$0")" || exit 1

PY=""
for candidate in python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c "import mlx_lm" >/dev/null 2>&1; then
            PY="$candidate"
            break
        fi
        [ -z "$PY" ] && PY="$candidate"
    fi
done

if [ -z "$PY" ]; then
    echo "No Python 3 found. Install it with:  brew install python"
    echo "Press any key to close..."; read -n 1; exit 1
fi

"$PY" app.py
status=$?

if [ $status -ne 0 ]; then
    echo ""
    echo "MLX Studio exited with status $status."
    echo "Press any key to close..."
    read -n 1
fi
