#!/usr/bin/env bash
# Create .env from .env.example when it does not exist
set -o errexit -o nounset

if [ -f .env ]; then
  echo ".env already exists; leaving it unchanged"
else
  cp .env.example .env
  echo "Created .env from .env.example"
fi
