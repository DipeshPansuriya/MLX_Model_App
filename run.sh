#!/bin/bash
# MLX Studio — terminal launcher
cd "$(dirname "$0")" || exit 1
exec python3 app.py "$@"
