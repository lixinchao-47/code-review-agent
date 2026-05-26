#!/bin/bash
set -e

if [ "${STREAMLIT_MODE:-true}" = "true" ]; then
    exec streamlit run app/streamlit_app.py \
        --server.port="${PORT:-8501}" \
        --server.address=0.0.0.0 \
        --server.headless=true
else
    exec python scripts/run.py
fi
