#!/bin/zsh
cd -- "${0:A:h:h}" || exit 1
exec python3 scripts/serve_demo.py
