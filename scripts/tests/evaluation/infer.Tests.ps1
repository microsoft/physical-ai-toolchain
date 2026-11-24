#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for evaluation/sil/infer.sh, the OSMO Isaac Lab evaluation flow. The script runs from a
# scratch payload tree whose Isaac runtime helpers resolve to stub interpreters, so the tests check checkpoint
# sources, the success gate, and task outputs without Isaac Sim or a GPU.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:InferPath = (Resolve-Path (Join-Path $PSScriptRoot '../../../evaluation/sil/infer.sh')).Path
    $script:ScratchRoot = Join-Path $PSScriptRoot '.scratch-infer'
    New-Item -ItemType Directory -Path $script:ScratchRoot -Force | Out-Null

    # The Isaac interpreter stub plays the evaluator, exporter, and replay: each writes what the real script writes.
    $script:IsaacStub = @'
args=("$@")
value_of() {
  local flag="$1" i
  for ((i = 0; i < ${#args[@]}; i++)); do
    [[ "${args[i]}" == "$flag" ]] && { printf '%s' "${args[i + 1]}"; return; }
  done
}
if [[ "$1" == "-m" ]]; then
  passed=true
  [[ "${FAKE_GATE_EXIT:-0}" == "0" ]] || passed=false
  printf '{"success_rate": %s, "passed": %s}\n' "${FAKE_SUCCESS_RATE:-1.0}" "$passed" > "$(value_of --output-metrics)"
  exit "${FAKE_GATE_EXIT:-0}"
fi
case "$1" in
  */export_policy.py)
    touch "$(value_of --output-dir)/policy.pt" "$(value_of --output-dir)/policy.onnx"
    ;;
  */play_policy.py)
    video_dir="$(dirname "$(value_of --model)")/videos/$(value_of --format)_play"
    mkdir -p "$video_dir"
    touch "$video_dir/rl-video-episode-0.mp4"
    printf '{}\n' > "$(value_of --output-metrics)"
    ;;
esac
'@

    function New-InferWorkspace {
        $workspace = Join-Path $script:ScratchRoot ([guid]::NewGuid().ToString('N'))
        $tree = Join-Path $workspace 'payload'
        $runtimeDir = Join-Path $tree 'training/rl/scripts'
        $inferDir = Join-Path $tree 'evaluation/sil'
        New-Item -ItemType Directory -Path $runtimeDir, $inferDir -Force | Out-Null
        Copy-Item -Path $script:InferPath -Destination (Join-Path $inferDir 'infer.sh')
        Set-Content -Path (Join-Path $runtimeDir 'isaac_python_prologue.sh') -Value @('python_cmd=(fake-isaac)', 'python_exec=fake-python')
        Set-Content -Path (Join-Path $runtimeDir 'setup_isaac_runtime.sh') -Value ':'

        $checkpoint = Join-Path $workspace 'policy'
        New-Item -ItemType Directory -Path $checkpoint -Force | Out-Null
        foreach ($name in 'agent_200.pt', 'agent_1000.pt', 'agent_50.pt') {
            Set-Content -Path (Join-Path $checkpoint $name) -Value $name
        }

        [PSCustomObject]@{
            Root       = $workspace
            Infer      = Join-Path $inferDir 'infer.sh'
            Checkpoint = $checkpoint
            Output     = Join-Path $workspace 'task-output'
        }
    }

    function Invoke-Infer {
        param(
            [Parameter(Mandatory)][PSCustomObject]$Workspace,
            [hashtable]$EnvVars = @{}
        )

        $env = @{
            TASK             = 'Isaac-Cartpole-v0'
            CHECKPOINT_URI   = $Workspace.Checkpoint
            INFERENCE_FORMAT = 'jit'
            OUTPUT_DIR       = $Workspace.Output
        }
        foreach ($key in $EnvVars.Keys) { $env[$key] = $EnvVars[$key] }
        Invoke-BashEntryScript -ScriptPath $Workspace.Infer -EnvVars $env -WorkDir (Join-Path $Workspace.Root 'run') `
            -Stubs @{ 'fake-isaac' = $script:IsaacStub; 'fake-python' = 'exit 0' } -ScriptArgs @('--headless')
    }
}

AfterAll {
    Remove-Item -Recurse -Force $script:ScratchRoot -ErrorAction SilentlyContinue
}

Describe 'infer.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'gates the newest checkpoint from a container path before export and replay' {
        $workspace = New-InferWorkspace

        $result = Invoke-Infer -Workspace $workspace -EnvVars @{ EVAL_EPISODES = '20'; SUCCESS_THRESHOLD = '0.7' }

        $result.ExitCode | Should -Be 0
        $gate = @($result.Calls | Where-Object { $_ -match '^fake-isaac -m evaluation\.sil\.policy_evaluation ' })
        $gate.Count | Should -Be 1
        $gate[0] | Should -Match '--model-path \S+/policy/agent_1000\.pt --task Isaac-Cartpole-v0 --framework skrl --eval-episodes 20 --num-envs 4 --success-threshold 0\.7 --output-metrics \S+/eval_metrics\.json --headless$'
        $order = @($result.Calls | ForEach-Object { if ($_ -match 'policy_evaluation') { 'gate' } elseif ($_ -match 'export_policy') { 'export' } elseif ($_ -match 'play_policy') { 'replay' } })
        $order | Should -Be @('gate', 'export', 'replay')
        Test-Path (Join-Path $workspace.Output 'metrics/eval_metrics.json') | Should -BeTrue
        Test-Path (Join-Path $workspace.Output 'videos/jit_play/rl-video-episode-0.mp4') | Should -BeTrue
    }

    It 'prefers best_agent.pt over numbered checkpoints' {
        $workspace = New-InferWorkspace
        Set-Content -Path (Join-Path $workspace.Checkpoint 'best_agent.pt') -Value 'best'

        $result = Invoke-Infer -Workspace $workspace

        $result.ExitCode | Should -Be 0
        ($result.Calls | Where-Object { $_ -match 'policy_evaluation' }) | Should -Match '--model-path \S+/policy/best_agent\.pt '
    }

    It 'fails a policy below the threshold after replaying and publishing its videos' {
        $workspace = New-InferWorkspace

        $result = Invoke-Infer -Workspace $workspace -EnvVars @{ FAKE_GATE_EXIT = '1'; FAKE_SUCCESS_RATE = '0.2' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'did not pass the success gate'
        @($result.Calls | Where-Object { $_ -match '^fake-python \S+/evaluation/metrics/upload_artifacts\.py$' }).Count | Should -Be 1
        Test-Path (Join-Path $workspace.Output 'videos/jit_play/rl-video-episode-0.mp4') | Should -BeTrue
        (Get-Content -Raw (Join-Path $workspace.Output 'metrics/eval_metrics.json') | ConvertFrom-Json).passed | Should -BeFalse
    }

    It 'rejects a container path that does not exist' {
        $workspace = New-InferWorkspace

        $result = Invoke-Infer -Workspace $workspace -EnvVars @{ CHECKPOINT_URI = '/policy-missing' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'checkpoint path /policy-missing does not exist in this container'
        @($result.Calls | Where-Object { $_ -match 'policy_evaluation' }).Count | Should -Be 0
    }
}
