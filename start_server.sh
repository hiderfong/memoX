#!/bin/bash
cd /work/memoX
# Load .env variables
set -a
source .env
set +a

exec .venv/bin/python -m uvicorn src.web.api:app --host 0.0.0.0 --port 8080
