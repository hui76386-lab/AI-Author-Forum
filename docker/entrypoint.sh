#!/bin/sh
set -eu

for directory in "${STATIC_ROOT:-/data/static}" "${MEDIA_ROOT:-/data/media}" "${STATIC_PUBLISH_ROOT:-/data/published}"; do
    mkdir -p "$directory"
    chown -R wagtail:wagtail "$directory"
done

exec gosu wagtail "$@"
