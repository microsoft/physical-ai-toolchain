"""Container packaging contract for the production backend."""

from __future__ import annotations

import ast
import http.server
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import threading
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from tests.conftest import HostedCaller


@pytest.mark.parametrize("mode", ["", "invalid-mode-private-value"])
def test_given_invalid_caller_mode_when_starting_frontend_then_fails_without_private_output(mode: str) -> None:
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    environment = {key: value for key, value in os.environ.items() if key != "DATAVIEWER_PROXY_KEY"}
    result = subprocess.run(
        ["/bin/sh", str(frontend / "start-nginx.sh")],
        env={**environment, "DATAVIEWER_CALLER_MODE": mode},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Invalid DATAVIEWER_CALLER_MODE" in result.stderr
    assert "invalid-mode-private-value" not in result.stdout + result.stderr


@pytest.fixture(scope="module")
def first_party_wheels(tmp_path_factory: pytest.TempPathFactory) -> Path:
    repository = Path(__file__).resolve().parents[4]
    build_root = tmp_path_factory.mktemp("first-party-wheels")
    wheels = build_root / "dist"
    for project_path in ("data-management/dataset-io", "data-management/curation", "evaluation/vlm_judge"):
        project = build_root / project_path
        shutil.copytree(
            repository / project_path,
            project,
            ignore=shutil.ignore_patterns("__pycache__", ".venv", "build", "*.egg-info", ".pytest_cache"),
        )
        result = subprocess.run(
            ["uv", "build", "--offline", "--wheel", "--out-dir", str(wheels), str(project)],
            cwd=build_root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    return wheels


def test_given_real_nginx_when_forwarding_signed_proof_then_production_auth_enforces_claims(
    hosted_caller: HostedCaller,
    hosted_client: TestClient,
) -> None:
    if shutil.which("docker") is None or shutil.which("node") is None:
        pytest.skip("Docker and Node are required for the real proxy boundary")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            case = parse_qs(urlsplit(self.path).query).get("case", ["ok"])[0]
            overrides = {
                "expired": {"exp": 1},
                "wrong-audience": {"aud": "00000000-0000-0000-0000-000000000099"},
                "unauthorized": {"oid": "00000000-0000-0000-0000-000000000099"},
            }
            key = hosted_caller.keys.rogue if case == "forged" else hosted_caller.keys.primary
            self.send_response(204)
            self.send_header("X-Dataviewer-Caller-Token", hosted_caller.token(overrides.get(case), key=key))
            self.end_headers()

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            headers = json.loads(self.rfile.read(length))
            response = hosted_client.get("/api/auth/context", headers=headers)
            self.send_response(response.status_code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    repository = Path(__file__).resolve().parents[4]
    try:
        result = subprocess.run(
            [
                "node",
                str(repository / "scripts/tests/security/fixtures/caller-proxy-fixture.mjs"),
                "run",
                str(repository),
            ],
            env={
                **os.environ,
                "CALLER_FIXTURE_ENDPOINT": f"http://host.docker.internal:{server.server_port}",
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        assert result.returncode == 0, result.stderr
        assert "assertions passed" in result.stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "project_path",
    [
        ".",
        "data-management/curation",
        "data-management/dataset-io",
        "data-management/viewer/backend",
        "evaluation",
        "evaluation/vlm_judge",
    ],
)
def test_given_first_party_project_when_reading_metadata_then_license_and_repository_are_declared(
    project_path: str,
) -> None:
    repository = Path(__file__).resolve().parents[4]
    project = tomllib.loads((repository / project_path / "pyproject.toml").read_text())["project"]

    assert project["license"] == "MIT"
    assert project["urls"]["Repository"] == "https://github.com/microsoft/physical-ai-toolchain"


@pytest.mark.parametrize(
    ("project_path", "package_names"),
    [
        (".", ("physical-ai-curation", "physical-ai-dataset-io")),
        ("data-management/curation", ("physical-ai-dataset-io",)),
        (
            "data-management/viewer/backend",
            ("physical-ai-curation", "physical-ai-dataset-io", "physical-ai-vlm-judge"),
        ),
        ("evaluation", ("physical-ai-dataset-io",)),
        ("evaluation/vlm_judge", ("physical-ai-dataset-io",)),
    ],
)
def test_given_first_party_dependencies_when_resolving_then_sources_remain_local(
    project_path: str, package_names: tuple[str, ...]
) -> None:
    repository = Path(__file__).resolve().parents[4]
    locations = {
        "physical-ai-curation": repository / "data-management/curation",
        "physical-ai-dataset-io": repository / "data-management/dataset-io",
        "physical-ai-vlm-judge": repository / "evaluation/vlm_judge",
    }
    project = repository / project_path
    manifest = tomllib.loads((project / "pyproject.toml").read_text())
    sources = manifest["tool"]["uv"]["sources"]
    for name in package_names:
        assert (project / sources[name]["path"]).resolve() == locations[name]
    if (project / "uv.lock").exists():
        lock = tomllib.loads((project / "uv.lock").read_text())
        packages = {package["name"]: package for package in lock["package"]}
        for name in package_names:
            assert (project / packages[name]["source"]["editable"]).resolve() == locations[name]


@pytest.mark.parametrize("profile", ["dataset-io", "curation", "vlm-judge"])
def test_given_built_wheels_when_imported_outside_checkout_then_package_boundaries_hold(
    first_party_wheels: Path, tmp_path: Path, profile: str
) -> None:
    distributions = ["physical_ai_dataset_io"]
    if profile != "dataset-io":
        distributions.append("physical_ai_" + profile.replace("-", "_"))
    wheels = [next(first_party_wheels.glob(f"{name}-*.whl")) for name in distributions]
    installed = tmp_path / "installed"
    result = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(installed),
            "--no-deps",
            "--no-index",
            *map(str, wheels),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    script = """
import importlib.abc
import importlib.metadata
import json
import sys
from pathlib import Path

installed, dependencies, profile = sys.argv[1:]
sys.path.insert(0, installed)
if profile != 'dataset-io':
    sys.path.append(dependencies)

forbidden = {'src', 'fastapi', 'starlette', 'azure', 'torch', 'transformers'}
if profile != 'vlm-judge':
    forbidden |= {'evaluation', 'vlm_judge', 'av', 'PIL'}
if profile != 'curation':
    forbidden.add('physical_ai_curation')
if profile == 'dataset-io':
    forbidden |= {'pydantic', 'pyarrow', 'pandas'}

class UnavailableRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in forbidden:
            raise ModuleNotFoundError('Optional runtime absent', name=fullname)

sys.meta_path.insert(0, UnavailableRuntime())
import physical_ai_dataset_io.lerobot as dataset_io
import physical_ai_dataset_io.media

dataset = Path.cwd() / 'dataset'
(dataset / 'meta').mkdir(parents=True)
(dataset / 'meta/info.json').write_text(json.dumps({
    'codebase_version': 'v2.1', 'fps': 30, 'total_episodes': 1,
    'data_path': 'data/{episode_index}.parquet',
    'video_path': 'videos/{video_key}/{episode_index}.mp4',
    'features': {'front': {'dtype': 'video'}},
}))
assert dataset_io.load_dataset_spec(dataset).total_episodes == 1
modules = [dataset_io]
names = ['physical-ai-dataset-io']
if profile == 'curation':
    import physical_ai_curation.application as application
    from physical_ai_curation.adapters.job_storage import LocalJobStore
    from physical_ai_curation.adapters.local_dataset import LocalDatasetResolver
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.jobs import JudgeJobs
    from physical_ai_curation.models import DatasetLabelsFile
    assert DatasetLabelsFile(dataset_id='test').episodes == {}
    modules.append(application)
    names.append('physical-ai-curation')
elif profile == 'vlm-judge':
    import evaluation.vlm_judge as judge
    from PIL import Image
    agent = judge.JudgeAgent(judge.EchoBackend(), config=judge.AgentConfig(n_outcome_samples=1))
    result = agent.judge(episode_id='test/0', instruction='Move cube',
                         frames=[Image.new('RGB', (32, 32)) for _ in range(8)])
    assert judge.JudgeResult.from_dict(result.to_dict()).episode_id == 'test/0'
    modules.append(judge)
    names.append('physical-ai-vlm-judge')
for module in modules:
    assert Path(module.__file__).is_relative_to(installed), module.__file__
for name in names:
    metadata = importlib.metadata.distribution(name).metadata
    assert metadata['License-Expression'] == 'MIT'
    assert 'Repository, https://github.com/microsoft/physical-ai-toolchain' in metadata.get_all('Project-URL')
assert not forbidden.intersection(sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(installed), sysconfig.get_path("purelib"), profile],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "retired_path",
    ["evaluation/vlm_judge/api.py", "data-management/viewer/backend/scripts/vlm_label_dataset.py"],
)
def test_given_canonical_curation_entrypoints_then_retired_duplicates_are_absent(retired_path: str) -> None:
    repository = Path(__file__).resolve().parents[4]

    assert not (repository / retired_path).exists()


def test_dependency_installation_is_isolated_from_runtime_layers() -> None:
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    base, stages = dockerfile.split("FROM base AS build\n", maxsplit=1)
    build, runtime = stages.split("FROM base AS runtime\n", maxsplit=1)

    install = "uv sync --frozen --no-dev --no-editable --inexact ${extras}"
    assert install in build
    assert 'ARG BACKEND_EXTRAS="azure,analysis,export,auth,yolo"' in build
    assert 'ARG UV_DEFAULT_INDEX="https://pypi.org/simple"' in build
    assert 'ARG JUDGE_EXTRAS=""' in build
    assert "uv sync --project /app/evaluation/vlm_judge" in build
    assert "uv sync" not in base
    assert "uv sync" not in runtime
    assert "COPY --from=build --chown=appuser:appuser /app/.venv /app/.venv" in runtime
    assert "chown -R" not in runtime
    assert runtime.count("COPY --from=build ") == 1
    assert "/root/.cache" not in runtime
    assert "COPY --chown=appuser:appuser data-management/viewer/backend/logging.json ./" in runtime
    assert "USER appuser" in runtime


def test_given_installed_helper_when_imported_then_api_storage_and_models_remain_unloaded(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    project = tmp_path / "project"
    project.mkdir()
    shutil.copyfile(backend / "pyproject.toml", project / "pyproject.toml")
    shutil.copytree(backend / "src", project / "src", ignore=shutil.ignore_patterns("__pycache__"))
    wheels = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "--offline", "--wheel", "--out-dir", str(wheels), str(project)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    installed = tmp_path / "installed"
    restore = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(installed),
            "--no-deps",
            "--no-index",
            str(next(wheels.glob("*.whl"))),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert restore.returncode == 0, restore.stderr
    script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
sys.path.append(sys.argv[2])
import src.caller_auth_helper as helper
assert Path(helper.__file__).is_relative_to(sys.argv[1])
assert 'src.api.main' not in sys.modules
assert not any(name.startswith(('src.api.storage', 'torch', 'transformers', 'evaluation', 'physical_ai_curation'))
               for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(installed), sysconfig.get_path("purelib")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_judge_profiles_install_from_their_own_lock_without_checkout_imports() -> None:
    backend = Path(__file__).resolve().parents[1]
    dockerfile = (backend / "Dockerfile").read_text()
    compose = (backend.parent / "docker-compose.yml").read_text()
    assert "WORKDIR /app/data-management/viewer/backend" in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/app/.venv" in dockerfile
    assert "COPY evaluation/vlm_judge/ /app/evaluation/vlm_judge/" in dockerfile
    assert "COPY data-management/curation/ /app/data-management/curation/" in dockerfile
    assert "COPY data-management/dataset-io/ /app/data-management/dataset-io/" in dockerfile
    assert 'ARG JUDGE_EXTRAS=""' in dockerfile
    assert "uv sync --project /app/evaluation/vlm_judge --frozen --no-dev --no-editable" in dockerfile
    assert "COPY data-management/viewer/backend/src/ ./src/" in dockerfile
    assert "PYTHONPATH" not in dockerfile
    assert "context: ../.." in compose.split("  frontend:")[0]
    assert "dockerfile: data-management/viewer/backend/Dockerfile" in compose


def test_build_upload_is_allowlisted_and_compose_keeps_jobs_durable() -> None:
    backend = Path(__file__).resolve().parents[1]
    ignore = backend / "Dockerfile.dockerignore"
    assert ignore.exists()
    patterns = ignore.read_text().splitlines()
    assert patterns[0] == "**"
    assert "!evaluation/vlm_judge/*.py" in patterns
    assert "!data-management/curation/pyproject.toml" in patterns
    assert "!data-management/curation/physical_ai_curation/*.py" in patterns
    assert "!data-management/curation/physical_ai_curation/adapters/*.py" in patterns
    assert "!data-management/dataset-io/pyproject.toml" in patterns
    assert "!data-management/dataset-io/physical_ai_dataset_io/*.py" in patterns
    assert not any(line.startswith("!") and ".env" in line for line in patterns)
    compose = (backend.parent / "docker-compose.yml").read_text()
    assert 'VLM_JUDGE_JOB_DIR: "/state/judge"' in compose
    assert 'VLM_JUDGE_CACHE_DIR: "/scratch/judge"' in compose
    assert "judge-state:/state" in compose
    assert 'VLM_JUDGE_ENABLED: "${VLM_JUDGE_ENABLED:-false}"' in compose
    deploy = (backend.parents[1] / "setup" / "deploy-dataviewer.sh").read_text()
    assert '"$SRC_DIR/backend/"' not in deploy
    assert '--build-arg "JUDGE_EXTRAS=${judge_extras}"' in deploy
    assert 'cp "$backend_dockerfile" "$backend_context/data-management/viewer/backend/Dockerfile"' in deploy
    assert (
        'cp "$REPO_ROOT/data-management/curation/pyproject.toml" "$backend_context/data-management/curation/"' in deploy
    )
    assert 'cp "$REPO_ROOT/data-management/curation/physical_ai_curation/"*.py' in deploy
    assert 'cp "$REPO_ROOT/data-management/curation/physical_ai_curation/adapters/"*.py' in deploy
    assert 'cp "$REPO_ROOT/data-management/dataset-io/pyproject.toml"' in deploy
    assert 'cp "$REPO_ROOT/data-management/dataset-io/physical_ai_dataset_io/"*.py' in deploy


def test_authenticated_deployment_recovery_fails_closed() -> None:
    backend = Path(__file__).resolve().parents[1]
    deploy = (backend.parents[1] / "setup" / "deploy-dataviewer.sh").read_text()
    terraform = (backend.parents[2] / "infrastructure/terraform/modules/dataviewer/container-apps.tf").read_text()

    update_helper = deploy.split("update_app_and_wait() {", 1)[1].split("\n}", 1)[0]
    recovery = deploy.split("recover_authenticated_rollout() {", 1)[1].split("\n}", 1)[0]
    authenticated_rollout = deploy.split("deploy_authenticated_pair() {", 1)[1].split("\n}", 1)[0]
    frontend_container = terraform.split('resource "azurerm_container_app" "frontend"', 1)[1].split(
        'resource "azapi_resource" "frontend_auth"',
        1,
    )[0]

    assert "revision=$(az containerapp update \\\n" in update_helper
    assert ") || return 1" in update_helper
    assert 'wait_for_ready_revision "$app" "$revision" || return 1' in update_helper
    assert 'if [[ "$old_rollout_secure" != "true" ]]; then' in recovery
    assert "close_frontend_ingress" in recovery
    assert "rollback_backend || recovery_failed=true" in recovery
    assert "restore_app_rollout \\\n" in recovery
    assert "|| recovery_failed=true" in recovery
    assert "restore_frontend_auth" not in deploy
    assert authenticated_rollout.index("configure_frontend_auth") < authenticated_rollout.index(
        "frontend_pending_manifest"
    )
    assert authenticated_rollout.index("close_frontend_ingress") < authenticated_rollout.index("backend_manifest")
    assert authenticated_rollout.index("configure_frontend_auth") < authenticated_rollout.index("backend_manifest")
    assert authenticated_rollout.index("verify_backend_auth_enforced") < authenticated_rollout.index(
        "frontend_pending_manifest"
    )
    assert authenticated_rollout.index("verify_backend_auth_enforced") < authenticated_rollout.index(
        '"$frontend_manifest"'
    )
    assert "verify_frontend_auth_enforced" in authenticated_rollout
    assert "verify_anonymous_request_denied" in authenticated_rollout
    assert '--header "X-MS-CLIENT-PRINCIPAL: ${forged_principal}"' in deploy
    assert "302|401|403) return 0" in deploy
    assert "DATAVIEWER_PROXY_KEY" not in frontend_container
    assert 'resource "azapi_resource" "frontend_auth"' in terraform
    assert 'unauthenticatedClientAction = "RedirectToLoginPage"' in terraform


def _deployment_function_source() -> str:
    backend = Path(__file__).resolve().parents[1]
    deploy = (backend.parents[1] / "setup" / "deploy-dataviewer.sh").read_text()
    return deploy.split("wait_for_ready_revision() {", 1)[1].split('\nSRC_DIR="$SCRIPT_DIR/../viewer"', 1)[0]


def _run_deployment_fault_harness(tmp_path: Path, scenario: str) -> list[str]:
    trace = tmp_path / "trace"
    harness = f"""
set -o nounset
error() {{ :; }}
warn() {{ :; }}
wait_for_ready_revision() {{
{_deployment_function_source()}
{scenario}
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        env={**os.environ, "TRACE": str(trace)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return trace.read_text().splitlines() if trace.exists() else []


def test_failed_container_app_update_does_not_check_stale_revision(tmp_path: Path) -> None:
    trace = _run_deployment_fault_harness(
        tmp_path,
        """
rg=test-rg
az() {
  printf '%s\n' "$*" >>"$TRACE"
  return 23
}
if update_app_and_wait test-app --image test-image; then
  exit 90
fi
""",
    )
    assert trace == [
        "containerapp update --name test-app --resource-group test-rg --image test-image "
        "--query properties.latestRevisionName --output tsv"
    ]


@pytest.mark.parametrize("legacy", [True, False])
def test_given_backend_revision_when_verifying_rollout_then_requires_named_mi_contract(
    tmp_path: Path,
    legacy: bool,
) -> None:
    caller = {
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "api_client_id": "00000000-0000-0000-0000-000000000002",
        "caller_object_id": "00000000-0000-0000-0000-000000000003",
        "caller_client_id": "00000000-0000-0000-0000-000000000004",
    }
    environment = [
        {"name": "DATAVIEWER_AUTH_PROVIDER", "value": "easy_auth"},
        {"name": "DATAVIEWER_AUTH_DISABLED", "value": "false"},
    ]
    if legacy:
        environment.append({"name": "DATAVIEWER_PROXY_KEY", "secretRef": "dataviewer-proxy-key"})
    else:
        environment.extend(
            {"name": name, "value": caller[field]}
            for name, field in {
                "DATAVIEWER_AZURE_TENANT_ID": "tenant_id",
                "DATAVIEWER_AZURE_CLIENT_ID": "api_client_id",
                "DATAVIEWER_CALLER_OBJECT_ID": "caller_object_id",
                "DATAVIEWER_CALLER_CLIENT_ID": "caller_client_id",
            }.items()
        )
    containers = [{"name": "backend", "env": environment}]
    if not legacy:
        containers.insert(0, {"name": "unrelated", "env": []})
    state = json.dumps({"properties": {"template": {"containers": containers}}})
    trace = _run_deployment_fault_harness(
        tmp_path,
        f"""
rg=test-rg
backend_app=backend
proxy_secret_name=dataviewer-proxy-key
caller_auth='{json.dumps(caller)}'
az() {{ printf '%s' '{state}'; }}
if verify_backend_auth_enforced; then echo accepted >>"$TRACE"; else echo denied >>"$TRACE"; fi
""",
    )
    assert trace == (["denied"] if legacy else ["accepted"])


@pytest.mark.parametrize("failed_role", ["backend", "frontend"])
def test_partial_secure_restore_attempts_both_apps_then_closes_ingress(tmp_path: Path, failed_role: str) -> None:
    trace = _run_deployment_fault_harness(
        tmp_path,
        f"failed_role={failed_role}\n"
        + """
old_rollout_secure=true
skip_backend=false
skip_frontend=false
frontend_app=frontend
old_frontend_image=old-frontend
old_frontend_proxy_secret_ref=dataviewer-proxy-key
old_frontend_pending_manifest='{}'
rollback_backend() { echo backend >>"$TRACE"; [[ "$failed_role" != "backend" ]]; }
restore_app_rollout() { echo frontend >>"$TRACE"; [[ "$failed_role" != "frontend" ]]; }
verify_frontend_auth_enforced() { echo auth >>"$TRACE"; return 0; }
verify_anonymous_request_denied() { echo probe >>"$TRACE"; return 0; }
close_frontend_ingress() { echo close >>"$TRACE"; return 0; }
if recover_authenticated_rollout; then exit 91; fi
""",
    )
    assert trace == ["backend", "frontend", "auth", "probe", "close"]


@pytest.mark.parametrize("input_status", [1, 142])
def test_given_operator_input_failure_when_running_auth_gate_then_secure_recovery_runs(
    tmp_path: Path,
    input_status: int,
) -> None:
    trace = _run_deployment_fault_harness(
        tmp_path,
        f"read() {{ return {input_status}; }}\n"
        + """
frontend_url=https://frontend.example.test
info() { :; }
old_rollout_secure=false
close_frontend_ingress() { echo close >>"$TRACE"; }
if operator_auth_gate; then exit 92; else recover_authenticated_rollout; fi
""",
    )
    assert trace == ["close"]


@pytest.mark.parametrize(
    ("role", "mode"),
    [("frontend", "pending"), ("frontend", "managed_identity"), ("frontend", "off"), ("backend", "managed_identity")],
)
def test_given_runtime_snapshot_when_building_manifest_then_reconciles_named_containers(
    tmp_path: Path,
    role: str,
    mode: str,
) -> None:
    caller = {
        "mode": "managed_identity",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "api_client_id": "00000000-0000-0000-0000-000000000002",
        "caller_object_id": "00000000-0000-0000-0000-000000000003",
        "caller_client_id": "00000000-0000-0000-0000-000000000004",
        "caller_identity_id": (
            "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/"
            "providers/Microsoft.ManagedIdentity/userAssignedIdentities/caller"
        ),
    }
    snapshot = {
        "name": "test-app",
        "properties": {
            "template": {
                "containers": [
                    {
                        "name": "caller-helper",
                        "image": "old-helper",
                        "env": [{"name": "STORAGE_BACKEND", "value": "azure"}],
                    },
                    {
                        "name": role,
                        "image": "old-image",
                        "resources": {"cpu": 0.25, "memory": "0.5Gi"},
                        "env": [
                            {"name": "DATAVIEWER_PROXY_KEY", "secretRef": "old-secret"},
                            {"name": "DATAVIEWER_CALLER_OBJECT_ID", "value": "stale-caller"},
                        ],
                    },
                ]
            }
        },
    }
    trace = _run_deployment_fault_harness(
        tmp_path,
        f"""
caller_auth='{json.dumps(caller)}'
identity_id=shared-storage-identity
storage_client_id=00000000-0000-0000-0000-000000000005
backend_fqdn=backend.example.test
build_app_manifest '{json.dumps(snapshot)}' {role} new-image {mode} helper-image >"$TRACE"
""",
    )
    manifest = json.loads("\n".join(trace))
    containers = {container["name"]: container for container in manifest["properties"]["template"]["containers"]}
    selected = containers[role]
    assert selected["image"] == "new-image"
    settings = {setting["name"]: setting.get("value") for setting in selected["env"]}
    assert "DATAVIEWER_PROXY_KEY" not in settings
    if role == "frontend":
        assert settings["DATAVIEWER_CALLER_MODE"] == mode
        assert "DATAVIEWER_CALLER_OBJECT_ID" not in settings
        assert settings["NGINX_BACKEND_HOST"] == "backend.example.test"
        if mode == "off":
            assert set(containers) == {"frontend"}
        else:
            helper = containers["caller-helper"]
            assert helper["image"] == "helper-image"
            assert helper["command"] == ["python", "-m", "src.caller_auth_helper"]
            assert {setting["name"] for setting in helper["env"]} == {
                "DATAVIEWER_AZURE_CLIENT_ID",
                "DATAVIEWER_CALLER_CLIENT_ID",
            }
            assert helper["probes"] == []
    else:
        assert set(containers) == {"backend"}
        assert settings["DATAVIEWER_CALLER_OBJECT_ID"] == caller["caller_object_id"]
        assert settings["STORAGE_BACKEND"] == "azure"
        assert settings["AZURE_CLIENT_ID"] == "00000000-0000-0000-0000-000000000005"


def test_unsafe_snapshot_closes_ingress_without_restoring_apps(tmp_path: Path) -> None:
    trace = _run_deployment_fault_harness(
        tmp_path,
        """
old_rollout_secure=false
skip_backend=false
skip_frontend=false
rollback_backend() { echo unexpected-backend >>"$TRACE"; return 1; }
restore_app_rollout() { echo unexpected-frontend >>"$TRACE"; return 1; }
close_frontend_ingress() { echo close >>"$TRACE"; return 0; }
recover_authenticated_rollout
""",
    )
    assert trace == ["close"]


def _run_deployment_cli_fixture(tmp_path: Path, arguments: list[str], scenario: str = "ok") -> tuple[int, list[dict]]:
    pty = pytest.importorskip("pty")
    prefix = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test"
    identity = f"{prefix}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/storage"
    caller = {
        "mode": "managed_identity",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "api_client_id": "00000000-0000-0000-0000-000000000002",
        "caller_object_id": "00000000-0000-0000-0000-000000000003",
        "caller_client_id": "00000000-0000-0000-0000-000000000004",
        "caller_identity_id": f"{prefix}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/caller",
    }
    backend_settings = {
        "DATAVIEWER_AUTH_PROVIDER": "easy_auth",
        "DATAVIEWER_AUTH_DISABLED": "false",
        "DATAVIEWER_AZURE_TENANT_ID": caller["tenant_id"],
        "DATAVIEWER_AZURE_CLIENT_ID": caller["api_client_id"],
        "DATAVIEWER_CALLER_OBJECT_ID": caller["caller_object_id"],
        "DATAVIEWER_CALLER_CLIENT_ID": caller["caller_client_id"],
        "AZURE_CLIENT_ID": "00000000-0000-0000-0000-000000000005",
    }
    frontend_settings = {"DATAVIEWER_CALLER_MODE": "managed_identity"}
    helper = {
        "name": "caller-helper",
        "image": f"registry.example.test/dataviewer-backend@sha256:{'b' * 64}",
        "command": ["python", "-m", "src.caller_auth_helper"],
        "args": [],
        "resources": {"cpu": 0.25, "memory": "0.5Gi"},
        "probes": [],
        "volumeMounts": [],
        "env": [
            {"name": name, "value": value}
            for name, value in {
                "DATAVIEWER_AZURE_CLIENT_ID": caller["api_client_id"],
                "DATAVIEWER_CALLER_CLIENT_ID": caller["caller_client_id"],
            }.items()
        ],
    }
    apps = {}
    for role, settings in [("backend", backend_settings), ("frontend", frontend_settings)]:
        container = {
            "name": role,
            "image": f"registry.example.test/dataviewer-{role}@sha256:{'a' * 64}",
            "resources": {"cpu": 0.25, "memory": "0.5Gi"},
            "env": [{"name": name, "value": value} for name, value in settings.items()],
        }
        identities = {identity: {}}
        containers = [container]
        if role == "frontend":
            identities[caller["caller_identity_id"]] = {}
            containers.insert(0, helper)
        apps[role] = {
            "name": role,
            "location": "eastus",
            "identity": {"type": "UserAssigned", "userAssignedIdentities": identities},
            "properties": {
                "latestRevisionName": f"{role}-old",
                "latestReadyRevisionName": f"{role}-old",
                "configuration": {
                    "ingress": {"external": role == "frontend", "targetPort": 8080 if role == "frontend" else 8000}
                },
                "template": {"containers": containers},
            },
        }
    if scenario in ["legacy", "legacy-failed-update"]:
        apps["backend"]["properties"]["template"]["containers"][0]["env"].append(
            {"name": "DATAVIEWER_PROXY_KEY", "secretRef": "legacy-secret"}
        )
    if scenario == "missing-helper":
        apps["frontend"]["properties"]["template"]["containers"].pop(0)
    if scenario == "wrong-initial-identity":
        del apps["frontend"]["identity"]["userAssignedIdentities"][caller["caller_identity_id"]]
    if scenario == "stale-snapshot":
        apps["backend"]["properties"]["latestReadyRevisionName"] = "backend-older"
    if scenario == "mutable-snapshot":
        apps["backend"]["properties"]["template"]["containers"][0]["image"] = (
            "registry.example.test/dataviewer-backend:mutable"
        )
    if scenario == "bootstrap-readiness":
        apps["backend"]["properties"]["template"]["containers"][0]["image"] = "bootstrap:latest"
        apps["frontend"]["properties"]["template"]["containers"] = [
            container
            for container in apps["frontend"]["properties"]["template"]["containers"]
            if container["name"] == "frontend"
        ]
    output = {
        "resource_group": {"value": {"name": "rg-test"}},
        "container_registry": {"value": {"name": "registry", "login_server": "registry.example.test"}},
        "dataviewer": {
            "value": {
                "backend": {"name": "backend", "fqdn": "backend.example.test"},
                "frontend": {"name": "frontend", "url": "https://frontend.example.test"},
                "identity": {"id": identity, "client_id": "00000000-0000-0000-0000-000000000005"},
                "caller_auth": caller,
                "entra_id": {"client_id": caller["api_client_id"], "tenant_id": caller["tenant_id"]},
            }
        },
    }
    if scenario == "invalid-caller":
        del caller["caller_object_id"]
    if scenario == "non-hosted":
        output["dataviewer"]["value"]["entra_id"] = None
        output["dataviewer"]["value"]["caller_auth"] = None
    (tmp_path / "terraform.tfstate").write_text("{}")
    (tmp_path / "outputs.json").write_text(json.dumps(output))
    (tmp_path / "apps.json").write_text(json.dumps(apps))
    binaries = tmp_path / "bin"
    binaries.mkdir()
    program = (
        f"#!{sys.executable}\n"
        + """
import json, os, pathlib, sys
root = pathlib.Path(os.environ["DEPLOY_FIXTURE"])
tool, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
scenario = os.environ["DEPLOY_SCENARIO"]
def option(name, default=""):
    return args[args.index(name)+1] if name in args else default
def trace(value):
    with (root / "trace.jsonl").open("a") as stream:
        stream.write(json.dumps(value) + "\\n")
if tool == "terraform":
    print((root / "outputs.json").read_text())
    sys.exit(0)
if tool == "curl":
    print("302", end="")
    sys.exit(0)
if tool == "sleep":
    sys.exit(0)
trace({"tool": tool, "args": args})
if tool == "docker":
    sys.exit(0)
apps = json.loads((root / "apps.json").read_text())
app = option("--name")
query = option("--query")
if args[:2] == ["acr", "repository"]:
    image = option("--image")
    digest = image.split("@")[-1] if "@" in image else "sha256:" + ("c" if "backend" in image else "d")*64
    if scenario == "missing-image" or (scenario == "missing-helper-image" and digest.endswith("b"*64)):
        sys.exit(13)
    print(digest)
elif args[:3] == ["ad", "app", "show"]:
    print("https://frontend.example.test/.auth/login/aad/callback")
elif args[:3] == ["ad", "sp", "show"]:
    print("true" if scenario == "assignment-required" else "false")
elif args[:3] == ["containerapp", "secret", "list"]:
    print("microsoft-provider-authentication-secret")
elif args[:3] == ["containerapp", "auth", "show"]:
    print(json.dumps({
        "platform": {"enabled": True},
        "globalValidation": {"unauthenticatedClientAction": "RedirectToLoginPage"}
    }))
elif args[:2] == ["containerapp", "show"]:
    state = apps[app]
    if scenario in ["lowercase-identities", "uppercase-identities"]:
        identities = state["identity"]["userAssignedIdentities"]
        state["identity"]["userAssignedIdentities"] = {
            (key.lower() if scenario == "lowercase-identities" else key.upper()): value
            for key, value in identities.items()
        }
    if query == "properties.latestReadyRevisionName":
        backend_image = apps["backend"]["properties"]["template"]["containers"][0]["image"]
        if scenario == "bootstrap-readiness" and app == "frontend" and backend_image == "bootstrap:latest":
            print("frontend-old")
        else:
            print(state["properties"]["latestReadyRevisionName"])
    elif query == "properties.configuration.ingress.external":
        print(str(state["properties"]["configuration"]["ingress"]["external"]).lower())
    else:
        print(json.dumps(state))
elif args[:2] == ["containerapp", "update"]:
    manifest = json.loads(pathlib.Path(option("--yaml")).read_text())
    containers = manifest["properties"]["template"]["containers"]
    mode = next((
        setting["value"] for container in containers for setting in container.get("env", [])
        if setting["name"] == "DATAVIEWER_CALLER_MODE"
    ), "backend")
    trace({"update": app, "mode": mode, "manifest": manifest})
    if scenario in ["failed-update", "failed-update-close-failed", "legacy-failed-update"] and app == "backend":
        sys.exit(17)
    if scenario == "failed-frontend" and app == "frontend":
        sys.exit(17)
    revision = app + "-new-" + mode
    manifest["properties"]["latestRevisionName"] = revision
    stale_ready = scenario == "stale-ready" and app == "backend"
    manifest["properties"]["latestReadyRevisionName"] = "stale-ready" if stale_ready else revision
    if scenario == "wrong-helper" and app == "frontend":
        next(container for container in containers if container["name"] == "caller-helper")["image"] = "wrong-image"
    if scenario == "wrong-helper-mount" and app == "frontend":
        helper = next(container for container in containers if container["name"] == "caller-helper")
        helper["volumeMounts"] = [{"volumeName": "storage", "mountPath": "/data"}]
    if scenario == "probe-defaults":
        for container in manifest["properties"]["template"]["containers"]:
            for probe in container.get("probes", []):
                probe["httpGet"].update({"httpHeaders": [], "host": None})
    apps[app] = manifest
    print(revision)
elif args[:2] == ["containerapp", "ingress"]:
    if scenario in ["close-failed", "failed-update-close-failed"] and args[2] == "disable":
        sys.exit(19)
    apps[app]["properties"]["configuration"]["ingress"]["external"] = args[2] == "enable"
(root / "apps.json").write_text(json.dumps(apps))
"""
    )
    for name in ["az", "terraform", "docker", "curl", "sleep"]:
        executable = binaries / name
        executable.write_text(program)
        executable.chmod(0o700)
    script = Path(__file__).resolve().parents[3] / "setup/deploy-dataviewer.sh"
    master, slave = pty.openpty()
    os.write(master, b"no\n" if scenario == "operator-rejected" else b"yes\n")
    try:
        result = subprocess.run(
            ["bash", str(script), "--tf-dir", str(tmp_path), "--tag", "fixture", *arguments],
            env={
                **os.environ,
                "PATH": f"{binaries}:{os.environ['PATH']}",
                "DEPLOY_FIXTURE": str(tmp_path),
                "DEPLOY_SCENARIO": scenario,
            },
            stdin=slave,
            stdout=slave,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        os.close(slave)
        os.close(master)
    trace_path = tmp_path / "trace.jsonl"
    records = [json.loads(line) for line in trace_path.read_text().splitlines()] if trace_path.exists() else []
    return result.returncode, records


@pytest.mark.parametrize(
    "arguments,updated",
    [
        (["--skip-build"], {"backend", "frontend"}),
        (["--skip-build", "--skip-backend"], {"frontend"}),
        (["--skip-build", "--skip-frontend"], {"backend"}),
        (["--skip-build", "--skip-backend", "--skip-frontend"], set()),
        (["--skip-update", "--local-build"], set()),
        (["--local-build"], {"backend", "frontend"}),
        (["--skip-update", "--local-build", "--skip-backend"], set()),
        (["--skip-update", "--local-build", "--skip-frontend"], set()),
        (["--skip-update", "--skip-build"], set()),
        (["--config-preview"], set()),
    ],
)
def test_given_deployment_selection_when_executing_script_then_only_selected_apps_change(
    tmp_path: Path,
    arguments: list[str],
    updated: set[str],
) -> None:
    status, records = _run_deployment_cli_fixture(tmp_path, arguments)
    assert status == 0
    changes = [record for record in records if "update" in record]
    assert {record["update"] for record in changes} == updated
    if "frontend" in updated:
        assert [record["mode"] for record in changes if record["update"] == "frontend"] == [
            "pending",
            "managed_identity",
        ]
        if "backend" not in updated:
            helper = next(
                container
                for container in changes[-1]["manifest"]["properties"]["template"]["containers"]
                if container["name"] == "caller-helper"
            )
            assert helper["image"].endswith("b" * 64)


@pytest.mark.parametrize("scenario", ["lowercase-identities", "uppercase-identities"])
def test_given_resource_id_casing_when_deploying_then_identity_contract_is_preserved(
    tmp_path: Path,
    scenario: str,
) -> None:
    status, records = _run_deployment_cli_fixture(tmp_path, ["--skip-build"], scenario)

    assert status == 0
    assert {record["update"] for record in records if "update" in record} == {"backend", "frontend"}


def test_given_bootstrap_backend_when_deploying_then_backend_precedes_frontend_readiness(
    tmp_path: Path,
) -> None:
    status, records = _run_deployment_cli_fixture(tmp_path, ["--skip-build"], "bootstrap-readiness")
    changes = [record for record in records if "update" in record]

    assert status == 0
    assert [(record["update"], record["mode"]) for record in changes] == [
        ("backend", "backend"),
        ("frontend", "pending"),
        ("frontend", "managed_identity"),
    ]


@pytest.mark.parametrize(
    "scenario,arguments",
    [
        ("legacy", ["--skip-backend"]),
        ("missing-helper", ["--skip-frontend"]),
        ("assignment-required", []),
        ("wrong-initial-identity", []),
        ("invalid-caller", []),
        ("missing-image", []),
        ("missing-helper-image", ["--skip-backend"]),
        ("stale-snapshot", ["--skip-backend"]),
        ("mutable-snapshot", ["--skip-frontend"]),
    ],
)
def test_given_incompatible_deployment_when_checked_then_no_app_mutation_occurs(
    tmp_path: Path,
    scenario: str,
    arguments: list[str],
) -> None:
    status, records = _run_deployment_cli_fixture(tmp_path, ["--skip-build", *arguments], scenario)
    assert status != 0
    assert not any("update" in record for record in records)
    assert not any(
        record.get("tool") == "az"
        and record["args"][:3]
        in [
            ["containerapp", "registry", "set"],
            ["containerapp", "ingress", "disable"],
            ["containerapp", "auth", "update"],
        ]
        for record in records
    )


@pytest.mark.parametrize(
    "scenario",
    [
        "failed-update",
        "failed-frontend",
        "stale-ready",
        "wrong-helper",
        "operator-rejected",
        "failed-update-close-failed",
        "wrong-helper-mount",
    ],
)
def test_given_staged_rollout_failure_when_recovering_then_only_mi_restore_or_closure_is_attempted(
    tmp_path: Path,
    scenario: str,
) -> None:
    status, records = _run_deployment_cli_fixture(tmp_path, ["--skip-build"], scenario)
    assert status != 0
    changes = [record for record in records if "update" in record]
    assert {record["update"] for record in changes} == {"backend", "frontend"}
    for change in changes:
        for container in change["manifest"]["properties"]["template"]["containers"]:
            settings = {item["name"]: item.get("value") for item in container.get("env", [])}
            assert "DATAVIEWER_PROXY_KEY" not in settings
            if container["name"] == "backend":
                assert settings["DATAVIEWER_AUTH_DISABLED"] == "false"
                assert settings["DATAVIEWER_CALLER_CLIENT_ID"]
    if scenario == "operator-rejected":
        assert changes[-1]["mode"] == "managed_identity"
        assert changes[-1]["manifest"]["properties"]["template"]["containers"][0]["image"].endswith("a" * 64)
    else:
        closures = [
            record for record in records if record.get("args", [])[:3] == ["containerapp", "ingress", "disable"]
        ]
        assert len(closures) >= 2


def test_given_platform_probe_defaults_when_verifying_rollout_then_semantic_manifest_matches(tmp_path: Path) -> None:
    status, _ = _run_deployment_cli_fixture(tmp_path, ["--skip-build"], "probe-defaults")
    assert status == 0


def test_given_legacy_snapshot_when_rollout_fails_then_no_legacy_restore_is_attempted(tmp_path: Path) -> None:
    status, records = _run_deployment_cli_fixture(tmp_path, ["--skip-build"], "legacy-failed-update")
    assert status != 0
    changes = [record for record in records if "update" in record]
    assert [(record["update"], record["mode"]) for record in changes] == [
        ("backend", "backend"),
    ]
    closures = [record for record in records if record.get("args", [])[:3] == ["containerapp", "ingress", "disable"]]
    assert len(closures) == 2


def test_given_non_hosted_deployment_when_reconciling_then_removes_caller_topology(tmp_path: Path) -> None:
    status, records = _run_deployment_cli_fixture(tmp_path, ["--skip-build"], "non-hosted")
    assert status == 0
    changes = [record for record in records if "update" in record]
    assert [(record["update"], record["mode"]) for record in changes] == [("backend", "backend"), ("frontend", "off")]
    for change in changes:
        manifest = change["manifest"]
        assert len(manifest["identity"]["userAssignedIdentities"]) == 1
        assert len(manifest["properties"]["template"]["containers"]) == 1
        settings = {
            item["name"]: item.get("value") for item in manifest["properties"]["template"]["containers"][0]["env"]
        }
        assert not any(name.startswith("DATAVIEWER_CALLER_") and name != "DATAVIEWER_CALLER_MODE" for name in settings)
        if change["update"] == "backend":
            assert settings["DATAVIEWER_AUTH_DISABLED"] == "true"
            assert settings["STORAGE_BACKEND"] == "azure"
    assert any(record.get("args", [])[-3:] == ["false", "--output", "none"] for record in records)


@pytest.mark.parametrize("secret_reference", [False, True])
def test_given_inherited_credential_when_generating_manifest_then_requires_secret_reference(
    tmp_path: Path,
    secret_reference: bool,
) -> None:
    setting = {"name": "AZURE_OPENAI_API_KEY"}
    setting.update({"secretRef": "judge-secret"} if secret_reference else {"value": "fixture-private-value"})
    snapshot = {"properties": {"template": {"containers": [{"name": "backend", "env": [setting]}]}}}
    trace = _run_deployment_fault_harness(
        tmp_path,
        f"""
caller_auth=null
identity_id=storage-identity
storage_client_id=00000000-0000-0000-0000-000000000005
backend_fqdn=backend.example.test
if build_app_manifest '{json.dumps(snapshot)}' backend image off image >/dev/null 2>"$TRACE.error"; then
    echo accepted >>"$TRACE"
else
    echo denied >>"$TRACE"
fi
""",
    )
    assert trace == (["accepted"] if secret_reference else ["denied"])
    assert "fixture-private-value" not in (tmp_path / "trace.error").read_text()


def test_browser_gate_cannot_reuse_live_services_or_resolve_dependencies() -> None:
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    config = (frontend / "playwright.config.ts").read_text()
    assert "--with" not in config
    assert "reuseExistingServer: !process.env.CI" not in config
    assert "--no-sync --frozen" in config
    assert "mkdtempSync" in config
    assert "STORAGE_BACKEND: 'local'" in config
    assert "--port 18000" in config
    assert "--port 18001" in config
    assert "--port 18002" in config
    assert "physical_ai_curation.api:build_app --factory" in config
    assert "evaluation.vlm_judge.api" not in config
    scenarios = (frontend / "e2e" / "accessibility.spec.ts").read_text()
    assert "http://127.0.0.1:8000/" not in scenarios


def test_given_no_http_extra_when_importing_evaluator_api_then_core_models_remain_available() -> None:
    root = Path(__file__).resolve().parents[4]
    script = """
import importlib.abc
import sys

class NoHttpExtra(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'starlette', 'fastapi'}:
            raise ModuleNotFoundError('HTTP extra absent', name=fullname.split('.')[0])

sys.meta_path.insert(0, NoHttpExtra())
from physical_ai_curation.api import JudgeRequest, build_router
assert JudgeRequest(dataset_id='synthetic', episode_index=0).episode_index == 0
assert callable(build_router)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(root), os.environ.get("PYTHONPATH")]))},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_given_no_curation_when_importing_evaluator_then_echo_scoring_is_independent(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[4]
    for relative in ("evaluation/pyproject.toml", "evaluation/vlm_judge/pyproject.toml"):
        manifest = tomllib.loads((repository / relative).read_text())
        dependencies = manifest["project"].get("dependencies", []) + manifest.get("dependency-groups", {}).get(
            "dev", []
        )
        assert not any(dependency.startswith("physical-ai-curation") for dependency in dependencies), relative
        assert "physical-ai-curation" not in manifest["tool"]["uv"].get("sources", {}), relative
    root = Path(__file__).resolve().parents[4]
    shutil.copytree(
        root / "evaluation" / "vlm_judge",
        tmp_path / "vlm_judge",
        ignore=shutil.ignore_patterns("__pycache__", ".venv", "build", "*.egg-info"),
    )
    script = """
import importlib.abc
import sys

sys.path.extend(sys.argv[1:])

class NoCuration(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'physical_ai_curation', 'src', 'api'}:
            raise ModuleNotFoundError('Curation is not installed', name=fullname.split('.')[0])

sys.meta_path.insert(0, NoCuration())
from PIL import Image
from vlm_judge import AgentConfig, EchoBackend, JudgeAgent, JudgeResult, JudgeService

agent = JudgeAgent(EchoBackend(), config=AgentConfig(n_outcome_samples=1))
result = agent.judge(
    episode_id='synthetic/episode_0',
    instruction='Move the object',
    frames=[Image.new('RGB', (32, 32)) for _ in range(8)],
)
assert JudgeResult.from_dict(result.to_dict()).episode_id == 'synthetic/episode_0'
assert callable(JudgeService.judge_episode)
from vlm_judge.task_findings import build_user_prompt, parse_label
assert parse_label('{"object": "cube"}') == {'object': 'cube'}
assert 'wrist' in build_user_prompt(n_frames=2, views=['wrist'], instruction='Move cube')
assert not any(name.startswith('physical_ai_curation') for name in sys.modules)
"""
    shutil.copytree(root / "data-management/dataset-io/physical_ai_dataset_io", tmp_path / "physical_ai_dataset_io")
    before = set(tmp_path.rglob("*"))
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(tmp_path), sysconfig.get_path("purelib")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("layout", ["v2.1", "v3-jsonl", "v3-parquet"])
def test_given_no_evaluator_when_applying_saved_evidence_then_replay_and_withdrawal_work(
    tmp_path: Path, layout: str
) -> None:
    import physical_ai_curation

    shutil.copytree(Path(physical_ai_curation.__file__).parent, tmp_path / "physical_ai_curation")
    shutil.copyfile(
        Path(physical_ai_curation.__file__).parents[1] / "tests/test_job_cli.py", tmp_path / "test_job_cli.py"
    )
    shutil.copytree(
        Path(__file__).resolve().parents[4] / "data-management/dataset-io/physical_ai_dataset_io",
        tmp_path / "physical_ai_dataset_io",
    )
    script = """
import asyncio
import importlib.abc
import json
import sys
from pathlib import Path

sys.path[:0] = [sys.argv[1], sys.argv[2]]
class NoEvaluator(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'evaluation', 'vlm_judge', 'src', 'torch', 'transformers', 'av', 'PIL'}:
            raise ModuleNotFoundError('Evaluator absent', name=fullname)
sys.meta_path.insert(0, NoEvaluator())
from physical_ai_curation.application import apply_judge_result
from physical_ai_curation.evidence import bind_job_evidence
from physical_ai_curation.jobs import JudgeJobs, fingerprint
from physical_ai_curation.adapters.job_storage import LocalJobStore
from physical_ai_curation.adapters.local_dataset import LocalDatasetResolver
from physical_ai_curation.adapters.local_storage import LocalCurationStorage
from physical_ai_curation.storage import plan_withdrawal

async def check():
    root = Path(sys.argv[1]) / 'dataset'
    layout = sys.argv[3]
    (root / 'meta').mkdir(parents=True)
    info = {
        'codebase_version': 'v2.1' if layout == 'v2.1' else 'v3.0', 'fps': 30, 'total_episodes': 1,
        'data_path': 'data/{episode_index}.parquet', 'video_path': 'videos/{video_key}/{episode_index}.mp4',
        'features': {'front': {'dtype': 'video', 'videos_path': 'videos/front/{episode_index}.mp4'}}
    }
    episode = {'episode_index': 3, 'length': 30, 'tasks': ['Move the cube']}
    if layout == 'v3-parquet':
        import pyarrow as arrow
        import pyarrow.parquet as parquet
        info['video_path'] = 'videos/{video_key}/{file_index}.mp4'
        episode.update({'videos/front/chunk_index': 0, 'videos/front/file_index': 3,
                        'videos/front/from_timestamp': 1.0, 'videos/front/to_timestamp': 2.0})
        (root / 'meta/episodes/chunk-000').mkdir(parents=True)
        parquet.write_table(arrow.Table.from_pylist([episode]), root / 'meta/episodes/chunk-000/file-000.parquet')
    else:
        (root / 'meta/episodes.jsonl').write_text(json.dumps(episode) + '\\n')
    (root / 'meta/info.json').write_text(json.dumps(info))
    (root / 'videos/front').mkdir(parents=True)
    (root / 'videos/front/3.mp4').write_bytes(b'synthetic-media')
    resolver = LocalDatasetResolver({'dataset': root})
    record, snapshot = await resolver.resolve('dataset', 3, principal_scope_id='actor')
    assert record.instruction == snapshot.instruction == 'Move the cube'
    if layout == 'v3-parquet':
        assert record.video_windows == {'front': (1.0, 2.0)}
    storage = LocalCurationStorage({'dataset': root})
    applications = []

    async def forbidden_inference(*args):
        raise AssertionError('Stored-evidence management must not infer')

    async def apply(job, target):
        applied = await apply_judge_result(storage, job, target)
        applications.append(target['result_id'])
        if len(applications) == 1:
            raise OSError('Synthetic checkpoint failure after label publication')
        return applied

    def reopen():
        return JudgeJobs(LocalJobStore(Path(sys.argv[1]) / 'jobs'), LocalDatasetResolver({'dataset': root}),
                         forbidden_inference, capacity_scope='test', apply_result=apply, curation_storage=storage)

    manager = reopen()
    assert (await manager.store.read())['jobs'] == {}
    try:
        await manager.get('missing', 'actor')
    except KeyError:
        pass
    else:
        raise AssertionError('Missing durable job must be explicit')
    async def seed_result(request):
        config = {'execution': {'backend': 'echo', 'model_id': 'echo'}, 'prompt_version': 'test'}
        job = await manager.submit('dataset', 'actor', [3], config, idempotency_key=request)
        async with manager.store.transaction() as state:
            saved = state['jobs'][job['id']]
            saved['status'] = 'succeeded'
            target = saved['targets'][0]
            target.update(status='succeeded', result_id=fingerprint([job['id'], target['input_key']]),
                          result={'episode_id': record.episode_id, 'instruction': snapshot.instruction,
                                  'judge_model': 'echo', 'prompt_version': 'test', 'n_frames': 2,
                                  'outcome_success': True, 'outcome_confidence': 1.0, 'outcome_n_valid_votes': 1,
                                  'progress_per_frame': [0, 100], 'voc': 1.0})
            target['evidence'] = bind_job_evidence(saved, target, target['result']).to_dict()
        return job

    job = await seed_result('bound')
    manager = reopen()
    results = await manager.results('dataset', 'actor', 3, snapshot_id=snapshot.snapshot_id)
    assert results[0]['applicability'] == 'current' and results[0]['evidence']['schema_version'] == 1
    assert (await manager.results('dataset', 'other', 3)) == []
    assert not applications
    await manager.request_application(job['id'], 'actor')
    assert await manager.apply_once()
    assert (await manager.get(job['id'], 'actor'))['targets'][0]['application_status'] == 'failed'
    first = (await storage.load_versioned('dataset')).value
    assert first.episodes['3'] == ['SUCCESS']
    manager = reopen()
    await manager.request_application(job['id'], 'actor')
    assert await manager.apply_once()
    assert (await manager.get(job['id'], 'actor'))['targets'][0]['application_status'] == 'succeeded'
    replay = (await storage.load_versioned('dataset')).value
    assert replay.provenance == first.provenance
    assert len(applications) == 2 and len(set(applications)) == 1
    stale = await seed_result('stale')
    (root / 'meta/info.json').write_text(json.dumps({**info, 'fps': 60}))
    await manager.request_application(stale['id'], 'actor')
    assert await manager.apply_once()
    assert (await manager.get(stale['id'], 'actor'))['targets'][0]['application_error'] == 'SavedInputError'
    assert len(applications) == 2
    projected, summary = plan_withdrawal(replay, {job['id']})
    assert 'SUCCESS' not in projected.episodes.get('3', [])
    assert summary['removable_fields'] > 0
    preview = await manager.preview_reset('dataset', 'actor')
    await manager.confirm_reset('dataset', 'actor', preview['id'])
    assert await manager.reset_once()
    assert 'SUCCESS' not in (await storage.load_versioned('dataset')).value.episodes.get('3', [])
    assert (await manager.results('dataset', 'actor', 3))[0]['applicability'] == 'withdrawn'
    assert not any(name.startswith(('evaluation', 'vlm_judge')) for name in sys.modules)
asyncio.run(check())
if sys.argv[3] == 'v2.1':
    import os
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    os.environ.pop('PYTEST_ADDOPTS', None)
    import pytest
    assert pytest.main([str(Path(sys.argv[1]) / 'test_job_cli.py'), '-q', '-p', 'no:cacheprovider']) == 0
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(tmp_path), sysconfig.get_path("purelib"), layout],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_given_curation_core_when_inspected_then_runtime_and_storage_are_adapter_owned() -> None:
    import physical_ai_curation

    core = Path(physical_ai_curation.__file__).parent
    forbidden = {"evaluation", "vlm_judge", "fastapi", "starlette", "azure", "src"}
    for source in core.glob("*.py"):
        if source.name in {"api.py", "run.py", "label_dataset.py"}:
            continue
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden, source.name
            elif isinstance(node, ast.Import):
                assert not forbidden.intersection(alias.name.split(".")[0] for alias in node.names), source.name
    storage = ast.parse((core / "storage.py").read_text())
    definitions = {node.name for node in storage.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
    assert not {"LocalCurationStorage", "write_conditional", "delete_conditional"}.intersection(definitions)
    saved_input = ast.parse((core / "saved_input.py").read_text())
    definitions = {node.name for node in saved_input.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
    assert not {"LocalSavedInputReader", "local_source_revision"}.intersection(definitions)
    dataset_io = Path(__file__).resolve().parents[4] / "data-management/dataset-io/physical_ai_dataset_io"
    forbidden |= {"physical_ai_curation", "torch", "transformers", "av", "PIL"}
    for source in dataset_io.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden, source.name
            elif isinstance(node, ast.Import):
                assert not forbidden.intersection(alias.name.split(".")[0] for alias in node.names), source.name
