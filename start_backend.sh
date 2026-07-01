#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/backend"

# Runtime input link policy: symlink only, no copy.
# This avoids visible duplicate input files under backend/runtime.
export LOCAL_WEB_INPUT_LINK_ORDER="symlink"
export LOCAL_WEB_ALLOW_INPUT_SYMLINKS="1"
export LOCAL_WEB_ALLOW_INPUT_HARDLINKS="0"
export LOCAL_WEB_INPUT_LINK_FALLBACK="error"

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
