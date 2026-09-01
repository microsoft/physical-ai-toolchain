#!/usr/bin/env bash
# Attach SBOM and an optional OpenVEX attestation to an already-built, signed image in ACR.
# Decoupled from build-aml-model-image.sh so security/compliance can refresh
# VEX dispositions without rebuilding.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") --image <digest-ref> [OPTIONS]

Attach an SBOM and, when configured, an OpenVEX document to a previously built
image. Does NOT rebuild, re-push, or re-sign. Safe to re-run; each invocation
publishes a new attestation as an OCI referrer of the supplied digest.

OPTIONS:
    -h, --help                Show this help
    --image REF               Digest-pinned image reference (REQUIRED)
                              Format: <acr>.azurecr.io/<repo>@sha256:<hex>
    --mode MODE               sigstore | notation (default: \$DEFAULT_VERIFY_MODE
                              or 'sigstore')
    --acr-name NAME           ACR name for 'az acr login' (default: parsed from
                              the image ref)
    --vex-file PATH           OpenVEX document (sigstore only; default:
                              ${DEFAULT_VEX_FILE:-<unset>})
    --sbom-file PATH          Reuse an existing SPDX-JSON SBOM instead of
                              generating one via syft
    --skip-sbom               Skip SBOM attestation
    --skip-vex                Skip OpenVEX attestation
    --config-preview          Print resolved configuration and exit

NOTES:
  * sigstore mode emits two cosign attestations: --type spdxjson (SBOM) and
    --type openvex (VEX).
  * notation mode emits the SBOM as an 'oras attach' referrer; OpenVEX has no
    notation equivalent in this repo and is skipped with a warning.
  * The caller is expected to be logged in to the correct Entra tenant
    (\`az login --tenant <id>\`). 'az acr login' runs automatically when --acr-name
    is supplied or parseable from the image ref.

EXAMPLES:
    # Attach an explicit VEX document to a previously built image
    $(basename "$0") --image acrfleetprod001.azurecr.io/act-pickplace@sha256:abc... \
      --vex-file <path/to/document.openvex.json>

    # Refresh just the VEX (skip SBOM regeneration)
    $(basename "$0") --image <ref> --skip-sbom \
      --vex-file <path/to/document.openvex.json>

    # Reuse an existing SBOM and attach the VEX
    $(basename "$0") --image <ref> --sbom-file ./sbom.spdx.json \
      --vex-file <path/to/document.openvex.json>

    # Notation mode (SBOM only; VEX skipped)
    $(basename "$0") --image <ref> --mode notation
EOF
}

image=""
mode=""
acr_name=""
vex_file=""
sbom_file=""
skip_sbom=false
skip_vex=false
config_preview=false

require_option_value() {
  [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    --image)           require_option_value "$@"; image="$2"; shift 2 ;;
    --mode)            require_option_value "$@"; mode="$2"; shift 2 ;;
    --acr-name)        require_option_value "$@"; acr_name="$2"; shift 2 ;;
    --vex-file)        require_option_value "$@"; vex_file="$2"; shift 2 ;;
    --sbom-file)       require_option_value "$@"; sbom_file="$2"; shift 2 ;;
    --skip-sbom)       skip_sbom=true; shift ;;
    --skip-vex)        skip_vex=true; shift ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$image" ]] || fatal "--image is required"
[[ "$image" =~ ^.+@sha256:[0-9a-f]{64}$ ]] ||
  fatal "--image must be digest-pinned: <acr>.azurecr.io/<repo>@sha256:<64 lowercase hex characters>"

mode="${mode:-${DEFAULT_VERIFY_MODE:-sigstore}}"
case "$mode" in
  sigstore|notation) ;;
  none) fatal "--mode=none is meaningless for attestation; signing must precede attest" ;;
  *) fatal "Invalid --mode: $mode (expected sigstore | notation)" ;;
esac

# Parse acr_name from the digest ref. The login server may be canonical
# (<name>.azurecr.io) or data-plane-suffixed (<name>.privatelink.azurecr.io,
# <name>.<region>.data.azurecr.io); 'az acr login --name' expects just the
# leftmost label, so take the host segment and strip everything after the first '.'.
if [[ -z "$acr_name" && "$image" == *.azurecr.io/* ]]; then
  acr_host="${image%%/*}"
  acr_name="${acr_host%%.*}"
fi

# Resolve vex_file against SCRIPT_DIR when a relative path is supplied.
vex_path="${vex_file:-${DEFAULT_VEX_FILE:-}}"
if [[ -n "$vex_path" && "$vex_path" != /* ]]; then
  vex_path="$(realpath -m "$SCRIPT_DIR/$vex_path")"
fi
if [[ -z "$vex_path" ]]; then
  skip_vex=true
fi

section "Configuration"
print_kv "Image"      "$image"
print_kv "Mode"       "$mode"
print_kv "ACR name"   "${acr_name:-<unset>}"
print_kv "SBOM file"  "${sbom_file:-<generate via syft>}"
print_kv "VEX file"   "${vex_path:-<unset>}"
print_kv "Skip SBOM"  "$skip_sbom"
print_kv "Skip VEX"   "$skip_vex"

if [[ "$config_preview" == "true" ]]; then
  exit 0
fi
if [[ "$skip_sbom" == "true" && ( "$skip_vex" == "true" || "$mode" == "notation" ) ]]; then
  fatal "Nothing to attest: SBOM is skipped and OpenVEX is unavailable or skipped in $mode mode"
fi
if [[ "$skip_vex" == "true" && -z "$vex_path" ]]; then
  warn "No VEX document configured; skipping OpenVEX attestation"
elif [[ "$mode" == "sigstore" && "$skip_vex" != "true" && ! -f "$vex_path" ]]; then
  fatal "VEX file not found: $vex_path"
fi

# Tool requirements depend on mode and skip flags.
case "$mode" in
  sigstore)
    require_tools cosign
    if [[ "$skip_vex" != "true" ]]; then
      require_tools jq
    fi
    ;;
  notation) require_tools oras ;;
esac
if [[ "$skip_sbom" != "true" && -z "$sbom_file" ]]; then
  require_tools syft
fi

if [[ "$mode" == "sigstore" && "$skip_vex" != "true" ]]; then
  image_digest="${image##*@}"
  jq -e --arg digest "$image_digest" '
    def non_empty_string:
      type == "string" and length > 0;
    def valid_timestamp:
      type == "string"
      and test(
        "^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
        + "T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\\.[0-9]+)?Z$"
      );
    def allowed_status:
      . == "under_investigation"
      or . == "not_affected"
      or . == "affected"
      or . == "fixed";
    def digest_purl:
      type == "string"
      and test("^pkg:oci/.+@sha256:[0-9a-f]{64}\\?.*repository_url=[^&]+");
    def duplicate_pairs:
      [
        .statements[] as $statement
        | $statement.products[]
        | [$statement.vulnerability.name, .["@id"]]
      ]
      | group_by(.)
      | map(select(length > 1));

    (.["@context"] | type == "string" and startswith("https://openvex.dev/ns/"))
    and (.["@id"] | non_empty_string)
    and (.author | non_empty_string)
    and (.version | type == "number" and . >= 1 and . == floor)
    and (.timestamp | valid_timestamp)
    and (
      (has("last_updated") | not)
      or (.last_updated | valid_timestamp)
    )
    and (.statements | type == "array" and length > 0)
    and all(
      .statements[];
      (.vulnerability.name | non_empty_string)
      and (.products | type == "array" and length > 0)
      and all(.products[]; (.["@id"] | digest_purl))
      and (.status | allowed_status)
      and (
        (has("timestamp") | not)
        or (.timestamp | valid_timestamp)
      )
      and (
        .status != "not_affected"
        or (
          .justification == "component_not_present"
          or .justification == "vulnerable_code_not_present"
          or .justification == "vulnerable_code_not_in_execute_path"
          or .justification == "vulnerable_code_cannot_be_controlled_by_adversary"
          or .justification == "inline_mitigations_already_exist"
        )
        and (.status_notes | non_empty_string)
      )
      and (
        .status != "affected"
        or (
          (.action_statement | non_empty_string)
          and (.status_notes | non_empty_string)
        )
      )
      and (
        .status != "fixed"
        or (.status_notes | non_empty_string)
      )
    )
    and ((duplicate_pairs | length) == 0)
    and any(
      .statements[].products[];
      (.["@id"] | contains("@" + $digest + "?"))
    )
  ' "$vex_path" >/dev/null ||
    fatal "OpenVEX document is invalid or does not identify image digest $image_digest: $vex_path"
fi

# Authenticate to ACR when we can identify the registry.
if [[ -n "$acr_name" ]]; then
  az acr login --name "$acr_name"
else
  warn "ACR name not parseable from image ref; assuming caller already ran 'az acr login'"
fi

# Generate SBOM if needed; clean up only when we created it ourselves.
# Trap covers SIGINT/SIGTERM as well so a Ctrl-C during 'syft' doesn't leak the tempfile.
generated_sbom=""
if [[ "$skip_sbom" != "true" && -z "$sbom_file" ]]; then
  generated_sbom="$(mktemp -t sbom.XXXXXX.spdx.json)"
  trap 'rm -f "$generated_sbom"' EXIT INT TERM
  info "Generating SBOM via syft → $generated_sbom"
  syft "$image" -o spdx-json > "$generated_sbom"
  sbom_file="$generated_sbom"
fi

case "$mode" in
  sigstore)
    if [[ "$skip_sbom" != "true" ]]; then
      section "Attest SBOM (cosign, spdxjson)"
      cosign attest --yes --predicate "$sbom_file" --type spdxjson "$image"
    fi
    if [[ "$skip_vex" != "true" ]]; then
      section "Attest OpenVEX (cosign, openvex)"
      cosign attest --yes --predicate "$vex_path" --type openvex "$image"
    fi
    ;;

  notation)
    if [[ "$skip_sbom" != "true" ]]; then
      section "Attach SBOM (oras, spdx+json)"
      # Notation has no native attestation primitive; SBOM rides as a referrer.
      oras attach \
        --artifact-type application/vnd.spdx+json \
        "$image" \
        "$sbom_file:application/spdx+json"
    fi
    if [[ "$skip_vex" != "true" ]]; then
      warn "OpenVEX attestation is not implemented for notation mode in this repo; skipping."
    fi
    ;;
esac

section "Attestation Summary"
print_kv "Image" "$image"
print_kv "Mode"  "$mode"
[[ "$skip_sbom" == "true" ]] || print_kv "SBOM attached" "yes"
[[ "$skip_vex" == "true" || "$mode" == "notation" ]] \
  || print_kv "VEX attached"  "yes"
info "Done."
