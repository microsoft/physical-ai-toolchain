#!/usr/bin/env bash
# Download LeRobot checkpoints from WANDB or HuggingFace Hub and register them as Azure ML models
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || dirname "$SCRIPT_DIR")"

source "$REPO_ROOT/deploy/002-setup/lib/common.sh"
source "$SCRIPT_DIR/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/deploy/001-iac" 2>/dev/null || true

# Source .env file if present (for credentials and Azure context)
ENV_FILE="${SCRIPT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

#------------------------------------------------------------------------------
# Help
#------------------------------------------------------------------------------

show_help() {
  cat << 'EOF'
Usage: register-checkpoints-to-aml.sh [OPTIONS]

Download LeRobot policy checkpoints from WANDB or HuggingFace Hub and
register them as versioned models in the Azure ML model registry.

SOURCE (one required):
    --from-wandb                  Download checkpoints from WANDB artifacts
    --from-hf                     Download checkpoints from HuggingFace Hub

WANDB OPTIONS (with --from-wandb):
    --wandb-entity ENTITY         WANDB entity/username
    --wandb-project PROJECT       WANDB project name
    --wandb-run-id ID             WANDB run ID (e.g., az9yre60)

HUGGINGFACE OPTIONS (with --from-hf):
    --hf-repo-id ID               HuggingFace model repo (e.g., user/model)
    --hf-revision REV             Repo revision/branch (default: main)

MODEL REGISTRATION:
    -n, --model-name NAME         AML model registry name
                                  (default: derived from source)
    --checkpoint STEP             Register only checkpoint at STEP (e.g., 022500)
    --latest-only                 Register only the latest checkpoint

GENERAL:
    --download-dir DIR            Local directory for downloads
                                  (default: /tmp/lerobot-checkpoints)
    --policy-type TYPE            Policy architecture tag (default: act)
    --job-name NAME               Training job name tag
    --source-workflow ID          OSMO workflow ID tag

AZURE CONTEXT:
    --subscription-id ID          Azure subscription ID
    --resource-group NAME         Azure resource group
    --workspace-name NAME         Azure ML workspace name

OTHER:
    --config-preview              Print configuration and exit
    --dry-run                     List checkpoints without registering
    -h, --help                    Show this help message

EXAMPLES:
    # Register all WANDB checkpoints from a run
    register-checkpoints-to-aml.sh \
      --from-wandb \
      --wandb-entity alizaidi \
      --wandb-project hve-robo-training \
      --wandb-run-id az9yre60

    # Register latest WANDB checkpoint only
    register-checkpoints-to-aml.sh \
      --from-wandb \
      --wandb-entity alizaidi \
      --wandb-project hve-robo-training \
      --wandb-run-id az9yre60 \
      --latest-only \
      -n hve-robo-act-train

    # Register all checkpoints from HuggingFace Hub
    register-checkpoints-to-aml.sh \
      --from-hf \
      --hf-repo-id alizaidi/hve-robo-act-train

    # Register a specific checkpoint step from WANDB
    register-checkpoints-to-aml.sh \
      --from-wandb \
      --wandb-entity alizaidi \
      --wandb-project hve-robo-training \
      --wandb-run-id az9yre60 \
      --checkpoint 055000 \
      --source-workflow lerobot-azure-data-training-15
EOF
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

source_type=""

wandb_entity="${WANDB_ENTITY:-}"
wandb_project="${WANDB_PROJECT:-}"
wandb_run_id="${WANDB_RUN_ID:-}"

hf_repo_id=""
hf_revision="main"

model_name=""
checkpoint_filter=""
latest_only=false
download_dir="/tmp/lerobot-checkpoints"
policy_type="act"
job_name=""
source_workflow=""
config_preview=false
dry_run=false

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              show_help; exit 0 ;;
    --from-wandb)           source_type="wandb"; shift ;;
    --from-hf)              source_type="hf"; shift ;;
    --wandb-entity)         wandb_entity="$2"; shift 2 ;;
    --wandb-project)        wandb_project="$2"; shift 2 ;;
    --wandb-run-id)         wandb_run_id="$2"; shift 2 ;;
    --hf-repo-id)           hf_repo_id="$2"; shift 2 ;;
    --hf-revision)          hf_revision="$2"; shift 2 ;;
    -n|--model-name)        model_name="$2"; shift 2 ;;
    --checkpoint)           checkpoint_filter="$2"; shift 2 ;;
    --latest-only)          latest_only=true; shift ;;
    --download-dir)         download_dir="$2"; shift 2 ;;
    --policy-type)          policy_type="$2"; shift 2 ;;
    --job-name)             job_name="$2"; shift 2 ;;
    --source-workflow)      source_workflow="$2"; shift 2 ;;
    --subscription-id)      subscription_id="$2"; shift 2 ;;
    --resource-group)       resource_group="$2"; shift 2 ;;
    --workspace-name)       workspace_name="$2"; shift 2 ;;
    --config-preview)       config_preview=true; shift ;;
    --dry-run)              dry_run=true; shift ;;
    *)                      fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

require_tools python3

[[ -z "$source_type" ]] && fatal "Specify --from-wandb or --from-hf"

if [[ "$source_type" == "wandb" ]]; then
  [[ -z "$wandb_entity" ]]  && fatal "--wandb-entity is required with --from-wandb"
  [[ -z "$wandb_project" ]] && fatal "--wandb-project is required with --from-wandb"
  [[ -z "$wandb_run_id" ]]  && fatal "--wandb-run-id is required with --from-wandb"
  [[ -z "$model_name" ]] && model_name=$(printf '%s' "$wandb_project" | tr '_' '-')
fi

if [[ "$source_type" == "hf" ]]; then
  [[ -z "$hf_repo_id" ]] && fatal "--hf-repo-id is required with --from-hf"
  [[ -z "$model_name" ]] && model_name=$(printf '%s' "$hf_repo_id" | sed 's|.*/||' | tr '_' '-')
fi

if [[ "$dry_run" == "false" ]]; then
  [[ -z "$subscription_id" ]] && fatal "Azure subscription ID required (set AZURE_SUBSCRIPTION_ID or deploy infra)"
  [[ -z "$resource_group" ]] && fatal "Azure resource group required (set AZURE_RESOURCE_GROUP or deploy infra)"
  [[ -z "$workspace_name" ]] && fatal "Azure ML workspace name required (set AZUREML_WORKSPACE_NAME or deploy infra)"
fi

#------------------------------------------------------------------------------
# Config Preview
#------------------------------------------------------------------------------

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Source" "$source_type"
  if [[ "$source_type" == "wandb" ]]; then
    print_kv "WANDB Entity" "$wandb_entity"
    print_kv "WANDB Project" "$wandb_project"
    print_kv "WANDB Run ID" "$wandb_run_id"
  else
    print_kv "HF Repo" "$hf_repo_id"
    print_kv "HF Revision" "$hf_revision"
  fi
  print_kv "Model Name" "$model_name"
  print_kv "Checkpoint" "${checkpoint_filter:-all}"
  print_kv "Latest Only" "$latest_only"
  print_kv "Download Dir" "$download_dir"
  print_kv "Policy Type" "$policy_type"
  print_kv "Job Name" "${job_name:-<not set>}"
  print_kv "Source Workflow" "${source_workflow:-<not set>}"
  print_kv "Subscription" "${subscription_id:-<not set>}"
  print_kv "Resource Group" "${resource_group:-<not set>}"
  print_kv "Workspace" "${workspace_name:-<not set>}"
  print_kv "Dry Run" "$dry_run"
  exit 0
fi

#------------------------------------------------------------------------------
# Download and Register
#------------------------------------------------------------------------------

mkdir -p "$download_dir"

if [[ "$source_type" == "wandb" ]]; then
  section "Registering checkpoints from WANDB"
  info "Run: $wandb_entity/$wandb_project/$wandb_run_id"
else
  section "Registering checkpoints from HuggingFace Hub"
  info "Repo: $hf_repo_id (revision: $hf_revision)"
fi

python3 - \
  "$source_type" "$model_name" "$download_dir" \
  "$checkpoint_filter" "$latest_only" "$dry_run" \
  "$policy_type" "$job_name" "$source_workflow" \
  "$subscription_id" "$resource_group" "$workspace_name" \
  "$wandb_entity" "$wandb_project" "$wandb_run_id" \
  "$hf_repo_id" "$hf_revision" << 'PYTHON_SCRIPT'
"""Download checkpoints from WANDB or HuggingFace Hub and register in Azure ML."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2


def register_models(
    model_paths: list[tuple[str, Path]],
    model_name: str,
    policy_type: str,
    job_name: str,
    source_workflow: str,
    source_tags: dict[str, str],
    subscription_id: str,
    resource_group: str,
    workspace_name: str,
) -> int:
    """Register downloaded checkpoint directories as AML models.

    Args:
        model_paths: List of (checkpoint_name, local_path) tuples.
        model_name: AML model registry name.
        policy_type: Policy architecture tag.
        job_name: Training job name tag.
        source_workflow: OSMO workflow ID tag.
        source_tags: Source-specific tags (wandb or hf metadata).
        subscription_id: Azure subscription ID.
        resource_group: Azure resource group name.
        workspace_name: Azure ML workspace name.

    Returns:
        Count of successfully registered models.
    """
    try:
        from azure.ai.ml import MLClient
        from azure.ai.ml.constants import AssetTypes
        from azure.ai.ml.entities import Model
        from azure.identity import DefaultAzureCredential
    except ImportError:
        logger.error("Azure ML SDK not installed. Run: pip install azure-ai-ml azure-identity")
        return 0

    credential = DefaultAzureCredential()
    ml_client = MLClient(
        credential=credential,
        subscription_id=subscription_id,
        resource_group_name=resource_group,
        workspace_name=workspace_name,
    )

    registered = 0
    for ckpt_name, model_dir in model_paths:
        has_weights = any(model_dir.rglob("*.safetensors")) or any(model_dir.rglob("*.bin"))
        if not has_weights:
            logger.warning("No model weights found in %s, skipping", model_dir)
            continue

        tags: dict[str, str] = {
            "framework": "lerobot",
            "policy_type": policy_type,
            "checkpoint": ckpt_name,
            **source_tags,
        }
        if job_name:
            tags["job_name"] = job_name
        if source_workflow:
            tags["source_workflow"] = source_workflow

        source_label = source_tags.get("wandb_run_path") or source_tags.get("hf_repo_id", "unknown")
        description = f"LeRobot {policy_type} policy checkpoint {ckpt_name} from {source_label}"

        model = Model(
            path=str(model_dir),
            name=model_name,
            description=description,
            type=AssetTypes.CUSTOM_MODEL,
            tags=tags,
        )

        try:
            result = ml_client.models.create_or_update(model)
            logger.info("Registered: %s v%s (checkpoint %s)", result.name, result.version, ckpt_name)
            registered += 1
        except Exception as e:
            logger.error("Failed to register %s: %s", ckpt_name, e)

    return registered


def extract_step_number(name: str) -> int:
    """Extract numeric step from an artifact or directory name.

    Args:
        name: Artifact name like 'policy_act-seed_1000-dataset_foo-055000:v0'.

    Returns:
        Step number, or 0 if not found.
    """
    match = re.search(r"-(\d{4,})", name)
    return int(match.group(1)) if match else 0


def process_wandb(
    wandb_entity: str,
    wandb_project: str,
    wandb_run_id: str,
    download_dir: Path,
    checkpoint_filter: str,
    latest_only: bool,
    dry_run: bool,
    model_name: str,
    policy_type: str,
    job_name: str,
    source_workflow: str,
    subscription_id: str,
    resource_group: str,
    workspace_name: str,
) -> int:
    """Download WANDB artifacts and register as AML models.

    Args:
        wandb_entity: WANDB entity/username.
        wandb_project: WANDB project name.
        wandb_run_id: WANDB run ID.
        download_dir: Local directory for downloads.
        checkpoint_filter: Filter string for checkpoint step.
        latest_only: Register only the latest checkpoint.
        dry_run: List without registering.
        model_name: AML model registry name.
        policy_type: Policy architecture tag.
        job_name: Training job name tag.
        source_workflow: OSMO workflow ID tag.
        subscription_id: Azure subscription ID.
        resource_group: Azure resource group name.
        workspace_name: Azure ML workspace name.

    Returns:
        Exit code.
    """
    try:
        import wandb
    except ImportError:
        logger.error("wandb not installed. Run: pip install wandb")
        return EXIT_FAILURE

    api = wandb.Api()
    run_path = f"{wandb_entity}/{wandb_project}/{wandb_run_id}"

    try:
        run = api.run(run_path)
    except Exception as e:
        logger.error("Failed to access WANDB run %s: %s", run_path, e)
        return EXIT_FAILURE

    logger.info("Run: %s (%s), state: %s", run.name, run_path, run.state)

    # Collect model artifacts sorted by step number
    artifacts = sorted(
        [a for a in run.logged_artifacts() if a.type == "model"],
        key=lambda a: extract_step_number(a.name),
    )

    if not artifacts:
        logger.error("No model artifacts found in run %s", run_path)
        return EXIT_FAILURE

    logger.info("Found %d checkpoint artifact(s)", len(artifacts))

    # Apply filters
    if checkpoint_filter:
        artifacts = [a for a in artifacts if checkpoint_filter in a.name]
        if not artifacts:
            logger.error("No artifact matching '%s'", checkpoint_filter)
            return EXIT_FAILURE

    if latest_only and len(artifacts) > 1:
        artifacts = [artifacts[-1]]

    # Display what we found
    for art in artifacts:
        step = extract_step_number(art.name)
        logger.info("  step %06d: %s (%.1f MB)", step, art.name, art.size / 1e6)

    if dry_run:
        logger.info("[DRY RUN] Would register %d checkpoint(s) as '%s'", len(artifacts), model_name)
        return EXIT_SUCCESS

    # Download and collect paths
    model_paths: list[tuple[str, Path]] = []
    for art in artifacts:
        step = extract_step_number(art.name)
        ckpt_name = f"{step:06d}"
        local_dir = download_dir / "wandb" / wandb_run_id / ckpt_name

        logger.info("Downloading artifact %s -> %s", art.name, local_dir)
        try:
            art.download(root=str(local_dir))
            model_paths.append((ckpt_name, local_dir))
        except Exception as e:
            logger.error("Failed to download %s: %s", art.name, e)

    if not model_paths:
        logger.error("No artifacts downloaded successfully")
        return EXIT_FAILURE

    source_tags = {
        "wandb_entity": wandb_entity,
        "wandb_project": wandb_project,
        "wandb_run_id": wandb_run_id,
        "wandb_run_path": run_path,
        "wandb_run_name": run.name,
    }

    registered = register_models(
        model_paths, model_name, policy_type, job_name, source_workflow,
        source_tags, subscription_id, resource_group, workspace_name,
    )

    if registered == 0:
        logger.error("No checkpoints registered")
        return EXIT_FAILURE

    logger.info("Registered %d checkpoint(s) as model '%s'", registered, model_name)
    return EXIT_SUCCESS


def process_hf(
    hf_repo_id: str,
    hf_revision: str,
    download_dir: Path,
    checkpoint_filter: str,
    latest_only: bool,
    dry_run: bool,
    model_name: str,
    policy_type: str,
    job_name: str,
    source_workflow: str,
    subscription_id: str,
    resource_group: str,
    workspace_name: str,
) -> int:
    """Download HuggingFace Hub checkpoints and register as AML models.

    Args:
        hf_repo_id: HuggingFace model repository ID.
        hf_revision: Repository revision/branch.
        download_dir: Local directory for downloads.
        checkpoint_filter: Filter string for checkpoint step.
        latest_only: Register only the latest checkpoint.
        dry_run: List without registering.
        model_name: AML model registry name.
        policy_type: Policy architecture tag.
        job_name: Training job name tag.
        source_workflow: OSMO workflow ID tag.
        subscription_id: Azure subscription ID.
        resource_group: Azure resource group name.
        workspace_name: Azure ML workspace name.

    Returns:
        Exit code.
    """
    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError:
        logger.error("huggingface_hub not installed. Run: pip install huggingface-hub")
        return EXIT_FAILURE

    api = HfApi()

    # List checkpoint directories in the repo
    try:
        repo_files = api.list_repo_tree(hf_repo_id, revision=hf_revision, repo_type="model")
        checkpoint_dirs = sorted(
            entry.rfile_path
            for entry in repo_files
            if hasattr(entry, "rfile_path")
            and entry.rfile_path.startswith("checkpoints/")
            and entry.rfile_path.count("/") == 1
        )
    except Exception:
        all_files = api.list_repo_files(hf_repo_id, revision=hf_revision, repo_type="model")
        checkpoint_dirs = sorted(
            {
                "/".join(f.split("/")[:2])
                for f in all_files
                if f.startswith("checkpoints/") and f.count("/") >= 2
            }
        )

    if not checkpoint_dirs:
        all_files = api.list_repo_files(hf_repo_id, revision=hf_revision, repo_type="model")
        has_model = any(f.endswith("model.safetensors") or f.endswith("pytorch_model.bin") for f in all_files)
        if has_model:
            checkpoint_dirs = [""]
            logger.info("No checkpoints/ directory found; treating repo root as the model")
        else:
            logger.error("No checkpoints found in %s", hf_repo_id)
            return EXIT_FAILURE

    logger.info("Found %d checkpoint(s) in %s", len(checkpoint_dirs), hf_repo_id)

    if checkpoint_filter:
        checkpoint_dirs = [d for d in checkpoint_dirs if checkpoint_filter in d]
        if not checkpoint_dirs:
            logger.error("No checkpoint matching '%s'", checkpoint_filter)
            return EXIT_FAILURE

    if latest_only and len(checkpoint_dirs) > 1:
        checkpoint_dirs = [checkpoint_dirs[-1]]

    for d in checkpoint_dirs:
        logger.info("  %s", d.split("/")[-1] if d else "root")

    if dry_run:
        logger.info("[DRY RUN] Would register %d checkpoint(s) as '%s'", len(checkpoint_dirs), model_name)
        return EXIT_SUCCESS

    # Download and collect paths
    model_paths: list[tuple[str, Path]] = []
    local_dir = download_dir / "hf" / hf_repo_id.replace("/", "_")

    for ckpt_path in checkpoint_dirs:
        ckpt_name = ckpt_path.split("/")[-1] if ckpt_path else "root"
        allow_patterns = (
            [f"{ckpt_path}/pretrained_model/*", f"{ckpt_path}/pretrained_model/**/*"]
            if ckpt_path
            else ["*.safetensors", "*.bin", "*.json", "config.yaml"]
        )

        try:
            snapshot_download(
                hf_repo_id, revision=hf_revision, local_dir=str(local_dir),
                allow_patterns=allow_patterns, repo_type="model",
            )
        except Exception:
            fallback = [f"{ckpt_path}/*", f"{ckpt_path}/**/*"] if ckpt_path else ["*"]
            try:
                snapshot_download(
                    hf_repo_id, revision=hf_revision, local_dir=str(local_dir),
                    allow_patterns=fallback, repo_type="model",
                )
            except Exception as e2:
                logger.error("Failed to download %s: %s", ckpt_name, e2)
                continue

        if ckpt_path:
            model_dir = local_dir / ckpt_path / "pretrained_model"
            if not model_dir.exists():
                model_dir = local_dir / ckpt_path
        else:
            model_dir = local_dir

        if model_dir.exists():
            model_paths.append((ckpt_name, model_dir))

    if not model_paths:
        logger.error("No checkpoints downloaded")
        return EXIT_FAILURE

    source_tags = {
        "hf_repo_id": hf_repo_id,
        "hf_revision": hf_revision,
    }

    registered = register_models(
        model_paths, model_name, policy_type, job_name, source_workflow,
        source_tags, subscription_id, resource_group, workspace_name,
    )

    if registered == 0:
        logger.error("No checkpoints registered")
        return EXIT_FAILURE

    logger.info("Registered %d checkpoint(s) as model '%s'", registered, model_name)
    return EXIT_SUCCESS


def main() -> int:
    """Route to WANDB or HuggingFace processor based on source type."""
    args = sys.argv[1:]
    if len(args) < 17:
        logger.error("Insufficient arguments from shell wrapper")
        return EXIT_ERROR

    source_type = args[0]
    model_name = args[1]
    download_dir = Path(args[2])
    checkpoint_filter = args[3]
    latest_only = args[4] == "true"
    dry_run = args[5] == "true"
    policy_type = args[6]
    job_name = args[7]
    source_workflow = args[8]
    subscription_id = args[9]
    resource_group = args[10]
    workspace_name = args[11]
    # Source-specific args
    wandb_entity = args[12]
    wandb_project = args[13]
    wandb_run_id = args[14]
    hf_repo_id = args[15]
    hf_revision = args[16]

    if source_type == "wandb":
        return process_wandb(
            wandb_entity, wandb_project, wandb_run_id,
            download_dir, checkpoint_filter, latest_only, dry_run,
            model_name, policy_type, job_name, source_workflow,
            subscription_id, resource_group, workspace_name,
        )
    elif source_type == "hf":
        return process_hf(
            hf_repo_id, hf_revision,
            download_dir, checkpoint_filter, latest_only, dry_run,
            model_name, policy_type, job_name, source_workflow,
            subscription_id, resource_group, workspace_name,
        )
    else:
        logger.error("Unknown source type: %s", source_type)
        return EXIT_ERROR


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        sys.exit(130)
    except BrokenPipeError:
        sys.stderr.close()
        sys.exit(1)
PYTHON_SCRIPT

exit_code=$?
if [[ $exit_code -ne 0 ]]; then
  fatal "Checkpoint registration failed (exit code: $exit_code)"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Summary"
print_kv "Source" "$source_type"
if [[ "$source_type" == "wandb" ]]; then
  print_kv "WANDB Run" "$wandb_entity/$wandb_project/$wandb_run_id"
else
  print_kv "HF Repo" "$hf_repo_id"
fi
print_kv "Model Name" "$model_name"
print_kv "Checkpoint" "${checkpoint_filter:-all}"
print_kv "Latest Only" "$latest_only"
print_kv "Workspace" "$workspace_name"
info "Checkpoint registration complete"
