#!/usr/bin/env bash
# Shared helpers for scripts that deploy the local OSMO profile onto a single-node K3s cluster.
#
# Source after scripts/lib/common.sh, data-pipeline/setup/defaults.conf, and
# infrastructure/local/defaults.conf. Manifest templates use ${LOCAL_*} placeholders only, so shell
# and Kubernetes syntax such as $VAR and $(VAR) inside a template is never substituted.

PHYSICAL_AI_SPEC_HASH_ANNOTATION="physical-ai-toolchain/spec-hash"

# Export every LOCAL_* setting so manifest rendering and child processes see the same values.
local_export_profile() {
  local name
  for name in $(compgen -v LOCAL_); do
    export "${name?}"
  done
}

# Verify the profile's kubeconfig and context identify a K3s cluster, then bind kubectl and helm to it.
local_cluster_activate() {
  verify_kube_target "$LOCAL_KUBECONFIG" "$LOCAL_KUBE_CONTEXT" k3s
  activate_kube_target "$LOCAL_KUBECONFIG" "$LOCAL_KUBE_CONTEXT"
}

# Create a namespace labeled as part of the local profile.
local_ensure_namespace() {
  local namespace="${1:?namespace required}"
  kubectl create namespace "$namespace" --dry-run=client -o json \
    | jq --arg part_of "$LOCAL_PART_OF" '.metadata.labels["app.kubernetes.io/part-of"] = $part_of' \
    | kubectl apply -f - >/dev/null
}

# Render a manifest template, substituting its ${LOCAL_*} placeholders from the environment.
local_render_manifest() {
  local template="${1:?template required}" placeholders="" names name
  [[ -f "$template" ]] || fatal "Manifest template not found: $template"
  # shellcheck disable=SC2016  # the patterns match literal ${LOCAL_*} placeholders
  names=$(grep -o '\${LOCAL_[A-Z0-9_]*}' "$template" | sed 's/^\${\(.*\)}$/\1/' | sort -u || true)
  for name in $names; do
    printenv "$name" >/dev/null || fatal "$(basename "$template") needs $name; export it before rendering"
    placeholders+=" \${$name}"
  done
  envsubst "$placeholders" < "$template"
}

# Ensure a Secret holds every listed key, generating only the missing values. Arguments after the name
# are KEY=BYTES pairs: BYTES random bytes as hex, or KEY=urlsafe:BYTES for unpadded URL-safe base64 (the
# shape of Python's secrets.token_urlsafe). Values never appear in process arguments or output, and
# existing values are never changed.
local_ensure_generated_secret() {
  local namespace="${1:?namespace required}" name="${2:?secret name required}"
  shift 2
  local current spec key work generated format
  local -a missing=() from_files=()

  current=$(kubectl -n "$namespace" get secret "$name" -o json --ignore-not-found)
  for spec in "$@"; do
    key="${spec%%=*}"
    if [[ -z "$current" ]] || ! jq -e --arg key "$key" '(.data[$key] // "") | length > 0' <<< "$current" >/dev/null; then
      missing+=("$spec")
    fi
  done
  (( ${#missing[@]} > 0 )) || return 0

  work=$(mktemp -d)
  chmod 0700 "$work"
  for spec in "${missing[@]}"; do
    key="${spec%%=*}"
    format="${spec#*=}"
    case "$format" in
      urlsafe:*) openssl rand -base64 "${format#urlsafe:}" | tr -d '\n=' | tr '+/' '-_' > "$work/$key" ;;
      *)         openssl rand -hex "$format" | tr -d '\n' > "$work/$key" ;;
    esac
    from_files+=("--from-file=$key=$work/$key")
  done
  generated=$(kubectl -n "$namespace" create secret generic "$name" "${from_files[@]}" --dry-run=client -o json)
  rm -rf "$work"

  if [[ -z "$current" ]]; then
    jq --arg part_of "$LOCAL_PART_OF" '.metadata.labels["app.kubernetes.io/part-of"] = $part_of' <<< "$generated" \
      | kubectl apply -f - >/dev/null
    info "Generated Secret $namespace/$name"
  else
    jq --slurpfile generated <(printf '%s' "$generated") '.data += $generated[0].data' <<< "$current" \
      | kubectl replace -f - >/dev/null
    info "Added ${#missing[@]} generated key(s) to Secret $namespace/$name"
  fi
}

# Create or update a Secret whose values come from keys of another Secret, plus optional non-secret
# literals, so consumers get credentials in the field names they read. Secret values move only through
# pipes, never through arguments or output.
# Usage: local_derive_secret <src-ns> <src-name> <dst-ns> <dst-name> <dst-key>=<src-key>... [-- <key>=<value>...]
local_derive_secret() {
  local src_namespace="${1:?source namespace required}" src_name="${2:?source secret required}"
  local dst_namespace="${3:?destination namespace required}" dst_name="${4:?destination secret required}"
  shift 4
  local mapping='{}' literals='{}' pair in_literals=false source

  for pair in "$@"; do
    if [[ "$pair" == "--" ]]; then
      in_literals=true
    elif [[ "$in_literals" == "true" ]]; then
      literals=$(jq --arg key "${pair%%=*}" --arg value "${pair#*=}" '. + {($key): ($value | @base64)}' <<< "$literals")
    else
      mapping=$(jq --arg key "${pair%%=*}" --arg value "${pair#*=}" '. + {($key): $value}' <<< "$mapping")
    fi
  done

  source=$(kubectl -n "$src_namespace" get secret "$src_name" -o json) \
    || fatal "Secret $src_namespace/$src_name not found"
  jq --arg name "$dst_name" --arg namespace "$dst_namespace" --arg part_of "$LOCAL_PART_OF" \
    --argjson mapping "$mapping" --argjson literals "$literals" '
    .data as $source
    | ($mapping | with_entries(.value = ($source[.value] // error("source Secret lacks key \(.value)")))) as $mapped
    | {apiVersion: "v1", kind: "Secret", type: "Opaque",
       metadata: {name: $name, namespace: $namespace, labels: {"app.kubernetes.io/part-of": $part_of}},
       data: ($mapped + $literals)}' <<< "$source" | kubectl apply -f - >/dev/null
}

# Build and push an image, then print its digest reference so a rollout pins exactly what was built.
# Callers run it in a command substitution, where errexit does not apply, so every step checks its own
# result and a failure returns non-zero with nothing printed on stdout.
# Usage: ref=$(local_build_and_push_image <repository> <tag> <dockerfile> <context> [build args...])
local_build_and_push_image() {
  local repository="${1:?repository required}" tag="${2:?tag required}" dockerfile="${3:?dockerfile required}"
  local context="${4:?build context required}" metadata digest
  shift 4
  metadata=$(mktemp) || return 1
  if ! docker buildx build --provenance=false --push --metadata-file "$metadata" \
    --tag "$repository:$tag" --file "$dockerfile" "$@" "$context" >&2; then
    rm -f "$metadata"
    error "Building or pushing $repository:$tag failed"
    return 1
  fi
  digest=$(jq -r '."containerimage.digest" // empty' "$metadata" 2>/dev/null || true)
  rm -f "$metadata"
  if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    error "The build of $repository:$tag reported no image digest"
    return 1
  fi
  printf '%s@%s\n' "$repository" "$digest"
}

# Apply a run-once Job from a rendered manifest and wait for it to succeed. A Job that already succeeded
# for the same manifest and hash input is kept, so a converged deployment starts nothing new.
# Usage: local_run_job <namespace> <job-name> <rendered-manifest> [hash-input] [timeout-seconds]
local_run_job() {
  local namespace="${1:?namespace required}" name="${2:?job name required}" manifest="${3:?manifest required}"
  local hash_input="${4:-}" timeout="${5:-300}" hash current status waited=0

  hash=$(printf '%s\n%s' "$manifest" "$hash_input" | sha256sum | cut -c1-16)
  current=$(kubectl -n "$namespace" get job "$name" -o json --ignore-not-found)
  if [[ -n "$current" ]]; then
    if jq -e --arg annotation "$PHYSICAL_AI_SPEC_HASH_ANNOTATION" --arg hash "$hash" \
      '.metadata.annotations[$annotation] == $hash and (.status.succeeded // 0) > 0' <<< "$current" >/dev/null; then
      info "Job $name already succeeded for this configuration"
      return 0
    fi
    kubectl -n "$namespace" delete job "$name" --wait=true >/dev/null
  fi

  jq --arg annotation "$PHYSICAL_AI_SPEC_HASH_ANNOTATION" --arg hash "$hash" \
    '.metadata.annotations[$annotation] = $hash' <<< "$(kubectl create --dry-run=client -o json -f - <<< "$manifest")" \
    | kubectl apply -f - >/dev/null
  info "Started Job $name"

  while (( waited < timeout )); do
    status=$(kubectl -n "$namespace" get job "$name" -o json \
      | jq -r 'if (.status.succeeded // 0) > 0 then "succeeded"
               elif any(.status.conditions[]?; .type == "Failed" and .status == "True") then "failed"
               else "running" end')
    case "$status" in
      succeeded) info "Job $name succeeded"; return 0 ;;
      failed) break ;;
    esac
    sleep 3
    waited=$(( waited + 3 ))
  done
  kubectl -n "$namespace" logs "job/$name" --tail=40 >&2 || true
  fatal "Job $name did not succeed${status:+ ($status)}; its last log lines are above"
}
