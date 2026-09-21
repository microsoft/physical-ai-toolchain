#!/usr/bin/env bash
# Run container and Kubernetes CPU smoke checks
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" delete pod/cpu-check --ignore-not-found >/dev/null
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" run cpu-check \
  --image=docker.io/library/alpine:3.22 \
  --restart=Never \
  --command -- sh -c 'uname -m; echo Kubernetes CPU pod works'
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" wait pod/cpu-check \
  --for=jsonpath='{.status.phase}'=Succeeded \
  --timeout=180s
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" logs pod/cpu-check
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" delete pod/cpu-check
