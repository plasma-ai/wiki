# The Linux container of the parity harness (SPEC.md, Container): the Ubuntu
# release of the CI runners, with the tools the harness and v1 start, each in
# that release's own version. The checkout is cloned in when a container
# starts, so the image holds no file of the repository and
# `python -m parity container` builds it with no context.
FROM ubuntu:26.04
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /usr/local/bin/
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates gawk git mawk tzdata \
    && rm -rf /var/lib/apt/lists/*
# a user of its own: v1's tests of unreadable files need one that is not root
RUN useradd --create-home parity \
    && install --directory --owner parity --group parity /work /cache
USER parity
ENV LANG=C.UTF-8 \
    UV_CACHE_DIR=/cache/uv \
    UV_PYTHON_INSTALL_DIR=/cache/python \
    UV_LINK_MODE=copy
WORKDIR /work
