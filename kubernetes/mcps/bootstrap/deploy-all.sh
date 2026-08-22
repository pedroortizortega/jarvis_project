#!/usr/bin/env bash
# Runs the full memory-router bootstrap in order. Safe to re-run: every
# step is idempotent (skips/rotates in place rather than failing on
# "already exists"), except the two steps that need an interactive sudo
# password (image import, /etc/hosts) — those stop with instructions
# instead of hanging, and you re-run this script after doing them by hand.
#
# See README.md in this directory for what each step does and why.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

./01-build-image.sh
./02-generate-pki.sh
./03-create-secrets.sh
./04-deploy-traefik-entrypoint.sh
./05-deploy-manifests.sh
./06-verify.sh
