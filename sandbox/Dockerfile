FROM python:3.12-slim

RUN useradd -m sandboxuser && \
    mkdir -p /sandbox && \
    chown sandboxuser:sandboxuser /sandbox

USER sandboxuser
WORKDIR /sandbox
