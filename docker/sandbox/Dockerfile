# Minimal sandbox image for personal-agent. Built lazily on first use (needs network once).
# To add tools: add `RUN pip install --no-cache-dir <pkg>` (or apt-get) below, then bump the tag in
# config/policy.yaml (sandbox.image) so the new image is built.
FROM python:3.12-slim

RUN useradd --create-home --uid 1000 sandbox \
 && mkdir /work && chown 1000:1000 /work

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
USER 1000:1000
WORKDIR /work
CMD ["sleep", "infinity"]
