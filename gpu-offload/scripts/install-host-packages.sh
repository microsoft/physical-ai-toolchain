#!/usr/bin/env bash
# Install the Ubuntu host packages required for local Kubernetes
set -o errexit -o nounset

sudo apt-get update
sudo apt-get install --yes curl jq podman uidmap
