#!/bin/sh
set -eu

private_root="${READER_PRIVATE_STORAGE_ROOT:-/data/protected-pdfs}"
mkdir -p "$private_root"
chown wagtail:wagtail "$private_root"

exec gosu wagtail "$@"
