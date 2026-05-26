"""Tests for the AzureML data-asset features added in feat/add-azureml-data-assets.

Covers:
* `parse_blob_url` URL validation (happy + sad paths).
* `download_dataset` path-traversal hardening (absolute, `..`, escaping symlink).
* `_register_model_via_aml` `dataset_source` lineage tag selection across the
  azureml-data-asset / azure-blob / mixed / huggingface code paths.
* Submission-script URI validation (`--dataset-asset`, `--init-from-policy-model`)
  for canonical-integer version, `@latest`, leading-zero rejection.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml
from conftest import load_training_module

_DOWNLOAD = load_training_module(
    "training_il_scripts_lerobot_download_dataset_assets",
    "training/il/scripts/lerobot/download_dataset.py",
)
_CHECKPOINTS = load_training_module(
    "training_il_scripts_lerobot_checkpoints_assets",
    "training/il/scripts/lerobot/checkpoints.py",
)


# ---------------------------------------------------------------------------
# parse_blob_url
# ---------------------------------------------------------------------------


class TestParseBlobUrl:
    def test_full_url_with_prefix(self):
        account, container, prefix = _DOWNLOAD.parse_blob_url("https://acct.blob.core.windows.net/cont/path/to/data")
        assert (account, container, prefix) == ("acct", "cont", "path/to/data")

    def test_url_without_prefix(self):
        account, container, prefix = _DOWNLOAD.parse_blob_url("https://acct.blob.core.windows.net/cont")
        assert (account, container, prefix) == ("acct", "cont", "")

    @pytest.mark.parametrize(
        "url",
        [
            "http://acct.blob.core.windows.net/cont/p",  # wrong scheme
            "https://acct.blob.core.windows.net",  # missing container
            "https://acct.example.com/cont/p",  # wrong host
            "ftp://acct.blob.core.windows.net/cont",  # non-https
            "",
        ],
    )
    def test_invalid_urls_raise(self, url):
        with pytest.raises(ValueError):
            _DOWNLOAD.parse_blob_url(url)


# ---------------------------------------------------------------------------
# download_dataset path-traversal hardening
# ---------------------------------------------------------------------------


def _install_azure_stubs(monkeypatch, list_blobs_return=(), download_payload=b"data"):
    azure_pkg = ModuleType("azure")
    azure_identity = ModuleType("azure.identity")
    azure_storage = ModuleType("azure.storage")
    azure_storage_blob = ModuleType("azure.storage.blob")

    azure_identity.DefaultAzureCredential = MagicMock(return_value="cred")

    download_stream = SimpleNamespace(readall=MagicMock(return_value=download_payload))
    container_client = SimpleNamespace(
        list_blobs=MagicMock(return_value=list(list_blobs_return)),
        download_blob=MagicMock(return_value=download_stream),
    )
    service_client = SimpleNamespace(
        get_container_client=MagicMock(return_value=container_client),
    )
    azure_storage_blob.BlobServiceClient = MagicMock(return_value=service_client)

    monkeypatch.setitem(sys.modules, "azure", azure_pkg)
    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity)
    monkeypatch.setitem(sys.modules, "azure.storage", azure_storage)
    monkeypatch.setitem(sys.modules, "azure.storage.blob", azure_storage_blob)

    return SimpleNamespace(container_client=container_client)


class TestDownloadDatasetTraversal:
    def test_skips_absolute_blob_name(self, monkeypatch, tmp_path, capsys):
        prefix = "p"
        blobs = [
            SimpleNamespace(name=f"{prefix}/safe.parquet"),
            SimpleNamespace(name=f"{prefix}//etc/passwd"),
        ]
        _install_azure_stubs(monkeypatch, list_blobs_return=blobs, download_payload=b"ok")

        result = _DOWNLOAD.download_dataset(
            storage_account="acct",
            storage_container="cont",
            blob_prefix=prefix,
            dataset_root=str(tmp_path),
            dataset_repo_id="user/ds",
        )

        assert (result / "safe.parquet").read_bytes() == b"ok"
        assert not (Path("/etc/passwd").exists() and (Path("/etc/passwd").read_bytes() == b"ok"))
        out = capsys.readouterr().out
        assert "Skipping unsafe blob name" in out or "Skipping blob outside dest_dir" in out

    def test_skips_dotdot_segments(self, monkeypatch, tmp_path, capsys):
        prefix = "p"
        blobs = [
            SimpleNamespace(name=f"{prefix}/../escape.parquet"),
            SimpleNamespace(name=f"{prefix}/a/../../escape2.parquet"),
        ]
        _install_azure_stubs(monkeypatch, list_blobs_return=blobs, download_payload=b"x")

        result = _DOWNLOAD.download_dataset(
            storage_account="acct",
            storage_container="cont",
            blob_prefix=prefix,
            dataset_root=str(tmp_path),
            dataset_repo_id="user/ds",
        )

        assert not (tmp_path / "escape.parquet").exists()
        assert not (tmp_path / "user" / "escape2.parquet").exists()
        assert list(result.rglob("*.parquet")) == []
        assert "Skipping unsafe blob name" in capsys.readouterr().out

    def test_skips_blob_resolving_outside_via_symlink(self, monkeypatch, tmp_path, capsys):
        # Pre-create a symlink inside the dest dir pointing outside it. A naive
        # implementation that joins paths but does not resolve symlinks would
        # follow the link and write to the external target.
        outside = tmp_path / "outside"
        outside.mkdir()
        dest_root = tmp_path / "root"
        dest_root.mkdir()
        repo_dir = dest_root / "user" / "ds"
        repo_dir.mkdir(parents=True)
        (repo_dir / "link").symlink_to(outside)

        blobs = [SimpleNamespace(name="p/link/escaped.parquet")]
        _install_azure_stubs(monkeypatch, list_blobs_return=blobs, download_payload=b"x")

        _DOWNLOAD.download_dataset(
            storage_account="acct",
            storage_container="cont",
            blob_prefix="p",
            dataset_root=str(dest_root),
            dataset_repo_id="user/ds",
        )

        assert not (outside / "escaped.parquet").exists()
        assert "Skipping blob outside dest_dir" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _register_model_via_aml lineage tag branches
# ---------------------------------------------------------------------------


@pytest.fixture
def azure_env(monkeypatch):
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-1")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-1")
    monkeypatch.setenv("AZUREML_WORKSPACE_NAME", "ws-1")
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_AUTHORITY_HOST", raising=False)


@pytest.fixture
def fake_azure_modules(monkeypatch):
    mlflow = ModuleType("mlflow")
    mlflow.log_artifacts = MagicMock()
    mlflow.set_tag = MagicMock()

    azure_pkg = ModuleType("azure")
    azure_ai = ModuleType("azure.ai")
    azure_ai_ml = ModuleType("azure.ai.ml")
    azure_constants = ModuleType("azure.ai.ml.constants")
    azure_entities = ModuleType("azure.ai.ml.entities")
    azure_identity = ModuleType("azure.identity")

    registered = SimpleNamespace(name="model-x", version="3")
    models_attr = SimpleNamespace(create_or_update=MagicMock(return_value=registered))
    client_instance = SimpleNamespace(models=models_attr)
    ml_client_cls = MagicMock(return_value=client_instance)
    credential_cls = MagicMock(return_value="cred")

    def model_factory(**kwargs):
        for key, value in kwargs.get("tags", {}).items():
            if len(str(value)) > 256:
                raise ValueError(f"Tag {key} exceeds AzureML value limit")
        return SimpleNamespace(**kwargs)

    azure_ai_ml.MLClient = ml_client_cls
    azure_constants.AssetTypes = SimpleNamespace(CUSTOM_MODEL="custom_model")
    model_cls = MagicMock(side_effect=model_factory)
    azure_entities.Model = model_cls
    azure_identity.DefaultAzureCredential = credential_cls

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "azure", azure_pkg)
    monkeypatch.setitem(sys.modules, "azure.ai", azure_ai)
    monkeypatch.setitem(sys.modules, "azure.ai.ml", azure_ai_ml)
    monkeypatch.setitem(sys.modules, "azure.ai.ml.constants", azure_constants)
    monkeypatch.setitem(sys.modules, "azure.ai.ml.entities", azure_entities)
    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity)

    return SimpleNamespace(model_cls=model_cls, models=models_attr)


def _clear_lineage_env(monkeypatch):
    for var in (
        "DATASET_REPO_ID",
        "BLOB_URLS",
        "DATASET_ASSETS",
        "STORAGE_ACCOUNT",
        "STORAGE_CONTAINER",
        "BLOB_PREFIX",
        "AZUREML_RUN_ID",
        "MLFLOW_RUN_ID",
        "MLFLOW_EXPERIMENT_ID",
        "JOB_NAME",
        "POLICY_TYPE",
        "REGISTER_CHECKPOINT",
    ):
        monkeypatch.delenv(var, raising=False)


class TestRegisterModelLineage:
    def test_data_asset_only_sets_azureml_data_asset(self, azure_env, fake_azure_modules, monkeypatch, tmp_path):
        _clear_lineage_env(monkeypatch)
        monkeypatch.setenv("DATASET_ASSETS", json.dumps(["azureml:ds:3"]))
        monkeypatch.setenv("REGISTER_CHECKPOINT", "m")

        _CHECKPOINTS._register_model_via_aml(tmp_path, "ckpt-001")
        tags = fake_azure_modules.model_cls.call_args.kwargs["tags"]
        assert tags["dataset_source"] == "azureml-data-asset"
        assert tags["dataset_uri"] == "azureml:ds:3"
        assert tags["dataset_assets"] == "azureml:ds:3"
        assert tags["dataset_asset_count"] == "1"
        assert tags["dataset_uri_count"] == "1"
        assert "blob_urls" not in tags
        lineage = json.loads((tmp_path / "azureml_lineage.json").read_text())
        assert lineage["dataset_assets"] == ["azureml:ds:3"]

    def test_multiple_data_assets_use_bounded_summary_tag(self, azure_env, fake_azure_modules, monkeypatch, tmp_path):
        _clear_lineage_env(monkeypatch)
        monkeypatch.setenv("DATASET_ASSETS", json.dumps(["azureml:a:1", "azureml:b:2"]))
        monkeypatch.setenv("REGISTER_CHECKPOINT", "m")

        _CHECKPOINTS._register_model_via_aml(tmp_path, "ckpt-001")
        tags = fake_azure_modules.model_cls.call_args.kwargs["tags"]
        assert tags["dataset_source"] == "azureml-data-asset"
        assert tags["dataset_asset_count"] == "2"
        assert tags["dataset_assets"].startswith("2 data assets; sha256:")
        assert tags["dataset_uri"].startswith("azureml-data-asset: 2 data asset(s), 0 blob URL(s); sha256:")
        lineage = json.loads((tmp_path / "azureml_lineage.json").read_text())
        assert lineage["dataset_assets"] == ["azureml:a:1", "azureml:b:2"]

    def test_blob_only_sets_azure_blob(self, azure_env, fake_azure_modules, monkeypatch, tmp_path):
        _clear_lineage_env(monkeypatch)
        monkeypatch.setenv(
            "BLOB_URLS",
            json.dumps(["https://acct.blob.core.windows.net/cont/prefix"]),
        )
        monkeypatch.setenv("REGISTER_CHECKPOINT", "m")

        _CHECKPOINTS._register_model_via_aml(tmp_path, "ckpt-001")
        tags = fake_azure_modules.model_cls.call_args.kwargs["tags"]
        assert tags["dataset_source"] == "azure-blob"
        assert tags["blob_urls"].startswith("https://acct.blob.core.windows.net/")
        assert tags["blob_url_count"] == "1"
        assert "dataset_assets" not in tags

    def test_mixed_sources_set_mixed(self, azure_env, fake_azure_modules, monkeypatch, tmp_path):
        _clear_lineage_env(monkeypatch)
        monkeypatch.setenv("DATASET_ASSETS", json.dumps(["azureml:ds:1"]))
        monkeypatch.setenv(
            "BLOB_URLS",
            json.dumps(["https://acct.blob.core.windows.net/cont/p"]),
        )
        monkeypatch.setenv("REGISTER_CHECKPOINT", "m")

        _CHECKPOINTS._register_model_via_aml(tmp_path, "ckpt-001")
        tags = fake_azure_modules.model_cls.call_args.kwargs["tags"]
        assert tags["dataset_source"] == "mixed"
        assert tags["dataset_uri"].startswith("mixed: 1 data asset(s), 1 blob URL(s); sha256:")
        assert tags["dataset_asset_count"] == "1"
        assert tags["blob_url_count"] == "1"
        lineage = json.loads((tmp_path / "azureml_lineage.json").read_text())
        assert lineage["dataset_assets"] == ["azureml:ds:1"]
        assert lineage["blob_urls"] == ["https://acct.blob.core.windows.net/cont/p"]

    def test_huggingface_fallback(self, azure_env, fake_azure_modules, monkeypatch, tmp_path):
        _clear_lineage_env(monkeypatch)
        monkeypatch.setenv("DATASET_REPO_ID", "user/ds")
        monkeypatch.setenv("REGISTER_CHECKPOINT", "m")

        _CHECKPOINTS._register_model_via_aml(tmp_path, "ckpt-001")
        tags = fake_azure_modules.model_cls.call_args.kwargs["tags"]
        assert tags["dataset_source"] == "huggingface"
        assert tags["dataset_uri"] == "hf://user/ds"
        assert tags["dataset_repo_id"] == "user/ds"
        lineage = json.loads((tmp_path / "azureml_lineage.json").read_text())
        assert lineage["dataset_repo_id"] == "user/ds"

    def test_malformed_dataset_assets_falls_back_gracefully(
        self, azure_env, fake_azure_modules, monkeypatch, tmp_path, capsys
    ):
        _clear_lineage_env(monkeypatch)
        monkeypatch.setenv("DATASET_ASSETS", "not-json")
        monkeypatch.setenv("DATASET_REPO_ID", "user/ds")
        monkeypatch.setenv("REGISTER_CHECKPOINT", "m")

        result = _CHECKPOINTS._register_model_via_aml(tmp_path, "ckpt-001")
        assert result is True
        tags = fake_azure_modules.model_cls.call_args.kwargs["tags"]
        # Malformed JSON should not break registration; HF fallback wins.
        assert tags["dataset_source"] == "huggingface"
        assert "dataset_assets" not in tags
        assert "Failed to parse DATASET_ASSETS" in capsys.readouterr().out

    def test_empty_dataset_assets_array_is_ignored(self, azure_env, fake_azure_modules, monkeypatch, tmp_path):
        _clear_lineage_env(monkeypatch)
        monkeypatch.setenv("DATASET_ASSETS", "[]")
        monkeypatch.setenv("DATASET_REPO_ID", "user/ds")
        monkeypatch.setenv("REGISTER_CHECKPOINT", "m")

        _CHECKPOINTS._register_model_via_aml(tmp_path, "ckpt-001")
        tags = fake_azure_modules.model_cls.call_args.kwargs["tags"]
        assert tags["dataset_source"] == "huggingface"
        assert "dataset_assets" not in tags

    def test_long_uri_lists_fit_azureml_tag_limit(self, azure_env, fake_azure_modules, monkeypatch, tmp_path):
        _clear_lineage_env(monkeypatch)
        assets = [
            "azureml://subscriptions/00000000-0000-0000-0000-000000000000/"
            f"resourceGroups/rg-long/workspaces/ws-long/data/dataset-{index}/versions/{index}"
            for index in range(1, 8)
        ]
        monkeypatch.setenv("DATASET_ASSETS", json.dumps(assets))
        monkeypatch.setenv("REGISTER_CHECKPOINT", "m")

        assert _CHECKPOINTS._register_model_via_aml(tmp_path, "ckpt-001") is True
        tags = fake_azure_modules.model_cls.call_args.kwargs["tags"]
        assert all(len(str(value)) <= 256 for value in tags.values())
        assert tags["dataset_asset_count"] == str(len(assets))
        assert tags["dataset_assets"].startswith("7 data assets; sha256:")
        lineage = json.loads((tmp_path / "azureml_lineage.json").read_text())
        assert lineage["dataset_assets"] == assets


# ---------------------------------------------------------------------------
# Submission-script URI validation (exercises the actual bash via subprocess
# so the regex stays in sync with shipped behaviour).
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUBMIT_SCRIPT = _REPO_ROOT / "training/il/scripts/submit-azureml-lerobot-training.sh"
_ENTRY_SCRIPT = _REPO_ROOT / "training/il/scripts/lerobot/azureml-train-entry.sh"


def _run_submit(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "AZURE_SUBSCRIPTION_ID": "sub",
            "AZURE_RESOURCE_GROUP": "rg",
            "AZUREML_WORKSPACE_NAME": "ws",
        }
    )
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(_SUBMIT_SCRIPT), *args, "--config-preview"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _run_submit_job(*args: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "AZURE_SUBSCRIPTION_ID": "sub",
            "AZURE_RESOURCE_GROUP": "rg",
            "AZUREML_WORKSPACE_NAME": "ws",
        }
    )
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(_SUBMIT_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _entrypoint_env(tmp_path: Path, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    venv = tmp_path / "lerobot-venv"

    _write_executable(
        fake_bin / "apt-get",
        """#!/usr/bin/env bash
exit 0
""",
    )
    _write_executable(
        fake_bin / "pip",
        """#!/usr/bin/env bash
exit 0
""",
    )
    _write_executable(
        fake_bin / "uv",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "venv" ]]; then
  venv=""
  for arg in "$@"; do
    venv="$arg"
  done
  mkdir -p "$venv/bin"
  printf 'export PATH="%s/bin:$PATH"\\n' "$venv" >"$venv/bin/activate"
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "python3",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-c" ]]; then
  exec "$REAL_PYTHON" "$@"
fi
if [[ "${1:-}" == "-m" ]]; then
  exit 0
fi
exec "$REAL_PYTHON" "$@"
""",
    )
    _write_executable(
        fake_bin / "lerobot-edit-dataset",
        """#!/usr/bin/env bash
set -euo pipefail
mode="${LEROBOT_EDIT_DATASET_MODE:-missing_info}"
dest=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--new_root" ]]; then
    dest="$2"
    shift 2
    continue
  fi
  shift
done
mkdir -p "$dest/meta"
case "$mode" in
  missing_info)
    ;;
  invalid_info)
    printf '{not-json' >"$dest/meta/info.json"
    ;;
  valid)
    printf '{"total_episodes":1,"total_frames":1,"features":{}}' >"$dest/meta/info.json"
    ;;
  *)
    echo "unknown LEROBOT_EDIT_DATASET_MODE=$mode" >&2
    exit 2
    ;;
esac
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "DATASET_ASSET_COUNT": "0",
            "BLOB_URLS": "[]",
            "DATASET_REPO_ID": "dataset",
            "DATASET_ROOT": str(tmp_path / "data"),
            "LEROBOT_VENV": str(venv),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "REAL_PYTHON": sys.executable,
        }
    )
    if extra:
        env.update(extra)
    return env


def _run_entrypoint(tmp_path: Path, *, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_ENTRY_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=_entrypoint_env(tmp_path, extra=extra),
        timeout=60,
    )


@pytest.mark.parametrize(
    "uri",
    [
        "azureml:ds:1",
        "azureml:ds:42",
        "azureml:ds:0",
        "azureml://subscriptions/x/resourceGroups/y/workspaces/z/data/ds/versions/7",
    ],
)
def test_dataset_asset_accepts_canonical_versions(uri):
    proc = _run_submit("--dataset-asset", uri, "--compute", "c")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "uri",
    [
        "azureml:ds:latest",
        "azureml:ds:@latest",
        "azureml:ds:01",
        "azureml:ds:1.0",
        "azureml:ds:",
        "azureml://subscriptions/x/resourceGroups/y/workspaces/z/data/ds/versions/latest",
        "azureml://subscriptions/x/resourceGroups/y/workspaces/z/data/ds/versions/01",
        "https://example.com/ds",  # not an azureml URI
    ],
)
def test_dataset_asset_rejects_non_canonical_versions(uri):
    proc = _run_submit("--dataset-asset", uri, "--compute", "c")
    assert proc.returncode != 0
    assert "--dataset-asset" in proc.stderr


@pytest.mark.parametrize(
    "uri",
    [
        "azureml:mdl:latest",
        "azureml:mdl:01",
        "azureml://subscriptions/x/resourceGroups/y/workspaces/z/models/mdl/versions/latest",
        "azureml://subscriptions/x/resourceGroups/y/workspaces/z/models/mdl/versions/02",
    ],
)
def test_init_from_policy_model_rejects_non_canonical(uri):
    proc = _run_submit(
        "--dataset-repo-id",
        "user/ds",
        "--compute",
        "c",
        "--init-from-policy-model",
        uri,
    )
    assert proc.returncode != 0
    assert "--init-from-policy-model" in proc.stderr


def test_missing_source_error_is_self_explanatory():
    proc = _run_submit("--compute", "c")
    assert proc.returncode != 0
    combined = (proc.stdout + proc.stderr).lower()
    assert "no dataset source" in combined
    assert "--dataset-repo-id" in (proc.stdout + proc.stderr)
    assert "--blob-url" in (proc.stdout + proc.stderr)
    assert "--dataset-asset" in (proc.stdout + proc.stderr)


def test_missing_compute_fails_fast_with_actionable_message():
    proc = _run_submit("--dataset-repo-id", "user/ds")
    assert proc.returncode != 0
    assert "--compute is required" in proc.stderr
    assert "AZUREML_COMPUTE" in proc.stderr


@pytest.mark.parametrize(
    "name",
    [
        "model name with spaces",
        "model/with/slashes",
        "model@latest",
        "-leading-dash",
        ".leading-dot",
        "x" * 256,
    ],
)
def test_register_checkpoint_rejects_invalid_names(name):
    proc = _run_submit(
        "--dataset-repo-id",
        "user/ds",
        "--compute",
        "c",
        "--register-checkpoint",
        name,
    )
    assert proc.returncode != 0
    assert "--register-checkpoint" in proc.stderr


@pytest.mark.parametrize(
    "name",
    [
        "cp",
        "lerobot_act_v1",
        "lerobot-act.v1",
        "_underscore-start",
        "X" * 255,
    ],
)
def test_register_checkpoint_accepts_valid_names(name):
    proc = _run_submit(
        "--dataset-repo-id",
        "user/ds",
        "--compute",
        "c",
        "--register-checkpoint",
        name,
    )
    assert proc.returncode == 0, proc.stderr


def test_job_submission_declares_mounted_inputs_in_rendered_yaml(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    captured_job_file = tmp_path / "captured-job.yml"
    az = fake_bin / "az"
    az.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

if [[ "$1" == "extension" && "$2" == "show" ]]; then
  exit 0
fi

if [[ "$1" == "ml" && "$2" == "environment" && "$3" == "create" ]]; then
  exit 0
fi

if [[ "$1" == "ml" && "$2" == "job" && "$3" == "create" ]]; then
  job_file=""
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--file" ]]; then
      job_file="$2"
      shift 2
      continue
    fi
    shift
  done
  cp "$job_file" "$CAPTURE_JOB_FILE"
  printf 'job-123\\n'
  exit 0
fi

echo "unexpected az call: $*" >&2
exit 2
""",
        encoding="utf-8",
    )
    az.chmod(0o755)

    proc = _run_submit_job(
        "--dataset-asset",
        "azureml:ds:1",
        "--init-from-policy-model",
        "azureml:model:2",
        "--compute",
        "c",
        env_extra={
            "CAPTURE_JOB_FILE": str(captured_job_file),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
    )

    assert proc.returncode == 0, proc.stderr
    rendered = captured_job_file.read_text(encoding="utf-8")
    parsed = yaml.safe_load(rendered)
    assert parsed["inputs"]["dataset_asset_0"] == {
        "type": "uri_folder",
        "mode": "ro_mount",
        "path": "azureml:ds:1",
    }
    assert parsed["inputs"]["init_from_policy_model"] == {
        "type": "custom_model",
        "mode": "download",
        "path": "azureml:model:2",
    }


def test_entrypoint_combines_sources_without_empty_array_nounset_expansion(tmp_path):
    proc = _run_entrypoint(tmp_path)
    assert proc.returncode == 0, proc.stderr

    entry_script = _ENTRY_SCRIPT.read_text(encoding="utf-8")
    assert 'all_sources=("${asset_paths[@]}" "${blob_paths[@]}")' not in entry_script
    assert "all_sources=()" in entry_script
    assert 'all_sources+=("${asset_paths[@]}")' in entry_script
    assert 'all_sources+=("${blob_paths[@]}")' in entry_script


def test_entrypoint_reports_missing_mounted_dataset_asset(tmp_path):
    mounted = tmp_path / "asset0"
    mounted.mkdir()
    proc = _run_entrypoint(
        tmp_path,
        extra={
            "DATASET_ASSET_COUNT": "2",
            "AZURE_ML_INPUT_dataset_asset_0": str(mounted),
        },
    )
    assert proc.returncode == 1
    assert "Expected 2 AzureML data asset mount(s), but found 1" in proc.stderr
    assert "AZURE_ML_INPUT_dataset_asset_1=<UNSET>" in proc.stderr


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("missing_info", "is missing info.json"),
        ("invalid_info", "is not valid JSON"),
    ],
)
def test_entrypoint_validates_merged_info_json(tmp_path, mode, expected):
    asset0 = tmp_path / "asset0"
    asset1 = tmp_path / "asset1"
    asset0.mkdir()
    asset1.mkdir()
    proc = _run_entrypoint(
        tmp_path,
        extra={
            "DATASET_ASSET_COUNT": "2",
            "AZURE_ML_INPUT_dataset_asset_0": str(asset0),
            "AZURE_ML_INPUT_dataset_asset_1": str(asset1),
            "LEROBOT_EDIT_DATASET_MODE": mode,
        },
    )
    assert proc.returncode == 1
    assert expected in proc.stderr


def test_entrypoint_merges_multiple_assets_when_info_json_is_valid(tmp_path):
    asset0 = tmp_path / "asset0"
    asset1 = tmp_path / "asset1"
    asset0.mkdir()
    asset1.mkdir()
    proc = _run_entrypoint(
        tmp_path,
        extra={
            "DATASET_ASSET_COUNT": "2",
            "AZURE_ML_INPUT_dataset_asset_0": str(asset0),
            "AZURE_ML_INPUT_dataset_asset_1": str(asset1),
            "LEROBOT_EDIT_DATASET_MODE": "valid",
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert "Merging 2 dataset sources" in proc.stdout


def test_entrypoint_downloads_blob_urls(tmp_path):
    proc = _run_entrypoint(
        tmp_path,
        extra={
            "DATASET_ASSET_COUNT": "0",
            "BLOB_URLS": json.dumps(["https://acct.blob.core.windows.net/cont/prefix"]),
            "DATASET_REPO_ID": "dataset",
            "DATASET_ROOT": str(tmp_path / "data"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert "Downloading datasets from Azure Blob Storage" in proc.stdout
    assert "Dataset materialized at:" in proc.stdout


def test_entrypoint_requires_dataset_repo_id_for_huggingface_fallback(tmp_path):
    proc = _run_entrypoint(
        tmp_path,
        extra={
            "DATASET_REPO_ID": "",
            "DATASET_ASSET_COUNT": "0",
            "BLOB_URLS": "[]",
        },
    )
    assert proc.returncode == 1
    assert "DATASET_REPO_ID is empty" in proc.stderr
    assert "--dataset-asset, --blob-url, or --dataset-repo-id" in proc.stderr
