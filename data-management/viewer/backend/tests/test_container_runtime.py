"""Container packaging contract for the production backend."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import sysconfig
import tomllib
from pathlib import Path

import pytest


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
    authenticated_rollout = deploy.split(
        'if [[ "$skip_update" == "false" && "$auth_enabled" == "true" ]]; then',
        1,
    )[1].split('elif [[ "$skip_update" == "false" ]]; then', 1)[0]
    frontend_rollout = authenticated_rollout.split('if [[ "$skip_frontend" == "false" ]]; then', 1)[1]
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
    assert frontend_rollout.index("az containerapp auth microsoft update") < frontend_rollout.index(
        '"DATAVIEWER_PROXY_KEY=secretref:${proxy_secret_name}"'
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


def test_partial_secure_restore_attempts_both_apps_then_closes_ingress(tmp_path: Path) -> None:
    trace = _run_deployment_fault_harness(
        tmp_path,
        """
old_rollout_secure=true
skip_backend=false
skip_frontend=false
frontend_app=frontend
old_frontend_image=old-frontend
old_frontend_proxy_secret_ref=dataviewer-proxy-key
rollback_backend() { echo backend >>"$TRACE"; return 1; }
restore_app_rollout() { echo frontend >>"$TRACE"; return 0; }
verify_frontend_auth_enforced() { echo auth >>"$TRACE"; return 0; }
verify_anonymous_request_denied() { echo probe >>"$TRACE"; return 0; }
close_frontend_ingress() { echo close >>"$TRACE"; return 0; }
recover_authenticated_rollout
""",
    )
    assert trace == ["backend", "frontend", "auth", "probe", "close"]


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
