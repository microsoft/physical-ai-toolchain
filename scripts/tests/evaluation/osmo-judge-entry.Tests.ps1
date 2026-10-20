#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for evaluation/vlm_judge/scripts/osmo-judge-entry.sh. The package installers are stubbed and
# the judge CLI is a stub that writes verdicts, while the entry's own verdict check runs on the real interpreter.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue) -and
        [bool](Get-Command python3 -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot '../../../evaluation/vlm_judge/scripts/osmo-judge-entry.sh')).Path
    $script:ScratchRoot = Join-Path $PSScriptRoot '.scratch-osmo-judge-entry'
    New-Item -ItemType Directory -Path $script:ScratchRoot -Force | Out-Null

    $script:Stubs = @{
        'pip' = 'exit 0'
        'uv'  = @'
if [[ "$1" == "sync" ]]; then
  [[ " $* " == *" --frozen "* && " $* " == *" --no-install-project "* && " $* " == *"/evaluation/vlm_judge"* ]] || exit 91
fi
exit 0
'@
        # The venv interpreter: the judge CLIs write verdicts, and the inline verdict check runs for real.
        'python' = @'
if [[ "$1" == "-" ]]; then
  exec python3 "$@"
fi
while [[ $# -gt 0 ]]; do
  [[ "$1" == "--output" ]] && output="$2"
  shift
done
[[ -n "${FAKE_VERDICT:-}" ]] && printf '%s\n' "$FAKE_VERDICT" > "$output"
exit "${FAKE_JUDGE_EXIT:-0}"
'@
    }

    # Encode an instruction the way submit-osmo-judge.sh does before the workflow carries it.
    function ConvertTo-InstructionB64 {
        param([Parameter(Mandatory)][string]$Text)
        [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Text))
    }

    function Invoke-JudgeEntry {
        param(
            [hashtable]$EnvVars = @{},
            [switch]$LeRobotInput
        )

        $workspace = Join-Path $script:ScratchRoot ([guid]::NewGuid().ToString('N'))
        $source = Join-Path $workspace 'archive-source'
        $inputRoot = Join-Path $workspace 'osmo-input'
        $judgeInput = Join-Path $workspace 'judge-input'
        New-Item -ItemType Directory -Path (Join-Path $source 'evaluation/vlm_judge'), $inputRoot, $judgeInput -Force | Out-Null
        Set-Content -Path (Join-Path $source 'evaluation/vlm_judge/uv.lock') -Value 'version = 1'
        Compress-Archive -Path (Join-Path $source '*') -DestinationPath (Join-Path $inputRoot 'payload.zip')
        if ($LeRobotInput) {
            New-Item -ItemType Directory -Path (Join-Path $judgeInput 'meta') -Force | Out-Null
            Set-Content -Path (Join-Path $judgeInput 'meta/info.json') -Value '{}'
        }
        $bashEnv = Join-Path $workspace '.bash_env'
        @('source() {', '  [[ "$1" == "/opt/judge-venv/bin/activate" ]] && return 0', '  builtin source "$@"', '}') |
            Set-Content -Path $bashEnv

        $env = @{
            BASH_ENV        = $bashEnv
            OSMO_INPUT_0    = $inputRoot
            JUDGE_INPUT_DIR = $judgeInput
            OUTPUT_DIR      = (Join-Path $workspace 'output')
            PAYLOAD_ROOT    = (Join-Path $workspace 'payload')
            FAKE_VERDICT    = '{"episode_id": "ds/episode_000000", "outcome_success": true, "outcome_confidence": 0.9, "progress_per_frame": [0, 50, 100]}'
        }
        foreach ($key in $EnvVars.Keys) { $env[$key] = $EnvVars[$key] }
        Invoke-BashEntryScript -ScriptPath $script:ScriptPath -EnvVars $env -Stubs $script:Stubs -WorkDir $workspace
    }
}

AfterAll {
    Remove-Item -Recurse -Force $script:ScratchRoot -ErrorAction SilentlyContinue
}

Describe 'osmo-judge-entry.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'judges a LeRobot dataset with the pinned Qwen3-VL model and reports the verdicts' {
        $result = Invoke-JudgeEntry -LeRobotInput -EnvVars @{
            JUDGE_MODEL_REVISION = 'ebb281ec70b05090aa6165b016eac8ec08e71b17'
            JUDGE_LIMIT          = '2'
        }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        ($result.Calls | Where-Object { $_ -match '^uv sync ' }) | Should -Match '--extra qwen3-vl$'
        $run = $result.Calls | Where-Object { $_ -match '^python -m evaluation\.vlm_judge\.run ' }
        $run | Should -Match '--dataset \S+/judge-input --output \S+/output/judgments\.partial\.jsonl --backend qwen3-vl'
        $run | Should -Match '--model-revision ebb281ec70b05090aa6165b016eac8ec08e71b17'
        $run | Should -Match '--limit 2'
        $result.StdOut | Should -Match 'JUDGE_RESULT=episodes=1 successes=1 mean_confidence=0\.900'
    }

    It 'judges rollout videos against the instruction with the echo backend' {
        $result = Invoke-JudgeEntry -EnvVars @{ JUDGE_BACKEND = 'echo'; JUDGE_INSTRUCTION_B64 = (ConvertTo-InstructionB64 'Keep the pole upright') }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        ($result.Calls | Where-Object { $_ -match '^uv sync ' }) | Should -Not -Match '--extra'
        ($result.Calls | Where-Object { $_ -match '^python -m evaluation\.vlm_judge\.policy_eval ' }) |
            Should -Match '--rollout-root \S+/judge-input --instruction=Keep the pole upright --output '
    }

    It 'passes an instruction with quotes and a backslash to the judge unchanged' {
        $text = 'Place the "red" cube \ in the bin'
        $result = Invoke-JudgeEntry -EnvVars @{ JUDGE_BACKEND = 'echo'; JUDGE_INSTRUCTION_B64 = (ConvertTo-InstructionB64 $text) }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        ($result.Calls | Where-Object { $_ -match '^python -m evaluation\.vlm_judge\.policy_eval ' }) |
            Should -Match ([regex]::Escape("--instruction=$text --output "))
    }

    It 'passes a one-word instruction that starts with a dash to the <Branch> judge as the option''s value' -ForEach @(
        @{ Branch = 'LeRobot'; LeRobotInput = $true; Cli = 'run' }
        @{ Branch = 'rollout'; LeRobotInput = $false; Cli = 'policy_eval' }
    ) {
        $result = Invoke-JudgeEntry -LeRobotInput:$LeRobotInput -EnvVars @{
            JUDGE_BACKEND         = 'echo'
            JUDGE_INSTRUCTION_B64 = (ConvertTo-InstructionB64 '--stop')
        }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        ($result.Calls | Where-Object { $_ -match "^python -m evaluation\.vlm_judge\.$Cli " }) |
            Should -Match ' --instruction=--stop( |$)'
    }

    It 'stops before installing when rollout videos have no instruction' {
        $result = Invoke-JudgeEntry

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'rollout videos need an instruction'
        @($result.Calls | Where-Object { $_ -match '^(pip|uv) ' }).Count | Should -Be 0
    }

    It 'fails when a verdict lacks per-frame progress' {
        $result = Invoke-JudgeEntry -LeRobotInput -EnvVars @{
            FAKE_VERDICT = '{"episode_id": "ds/episode_000000", "outcome_success": false, "outcome_confidence": 0.4, "progress_per_frame": []}'
        }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'incomplete verdict for ds/episode_000000'
        $result.StdErr | Should -Match 'failed their check; they stay in judgments\.partial\.jsonl'
        Test-Path -Path (Join-Path $result.WorkDir 'output/judgments.partial.jsonl') | Should -BeTrue
        Test-Path -Path (Join-Path $result.WorkDir 'output/judgments.jsonl') | Should -BeFalse
    }

    It 'fails when a verdict has no outcome' {
        $result = Invoke-JudgeEntry -LeRobotInput -EnvVars @{
            FAKE_VERDICT = '{"episode_id": "ds/episode_000000", "outcome_confidence": 0.9, "progress_per_frame": [0, 100]}'
        }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'incomplete verdict for ds/episode_000000'
        Test-Path -Path (Join-Path $result.WorkDir 'output/judgments.jsonl') | Should -BeFalse
    }

    It 'fails when an outcome is not true, false, or null' {
        $result = Invoke-JudgeEntry -LeRobotInput -EnvVars @{
            FAKE_VERDICT = '{"episode_id": "ds/episode_000000", "outcome_success": "yes", "outcome_confidence": 0.9, "progress_per_frame": [0, 100]}'
        }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'incomplete verdict for ds/episode_000000'
    }

    It 'accepts an inconclusive verdict and counts it' {
        $result = Invoke-JudgeEntry -LeRobotInput -EnvVars @{
            FAKE_VERDICT = @(
                '{"episode_id": "ds/episode_000000", "outcome_success": null, "outcome_confidence": 0.5, "progress_per_frame": [0, 40]}'
                '{"episode_id": "ds/episode_000001", "outcome_success": true, "outcome_confidence": 0.9, "progress_per_frame": [0, 100]}'
            ) -join "`n"
        }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'JUDGE_RESULT=episodes=2 successes=1 mean_confidence=0\.700 inconclusive=1'
        $result.StdOut | Should -Match 'ds/episode_000000: success=inconclusive confidence=0\.500'
    }

    It 'succeeds for a complete batch whose verdict is a failure' {
        $result = Invoke-JudgeEntry -LeRobotInput -EnvVars @{
            FAKE_VERDICT = '{"episode_id": "ds/episode_000000", "outcome_success": false, "outcome_confidence": 0.8, "progress_per_frame": [0, 20]}'
        }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'JUDGE_RESULT=episodes=1 successes=0 mean_confidence=0\.800 inconclusive=0'
        Test-Path -Path (Join-Path $result.WorkDir 'output/judgments.jsonl') | Should -BeTrue
        Test-Path -Path (Join-Path $result.WorkDir 'output/judgments.partial.jsonl') | Should -BeFalse
    }

    It 'requires the rollout judge to judge every selected rollout' {
        $result = Invoke-JudgeEntry -EnvVars @{ JUDGE_BACKEND = 'echo'; JUDGE_INSTRUCTION_B64 = (ConvertTo-InstructionB64 'Keep the pole upright') }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        ($result.Calls | Where-Object { $_ -match '^python -m evaluation\.vlm_judge\.policy_eval ' }) | Should -Match ' --require-all$'
    }

    It 'fails after reporting the verdicts a failed <Branch> judge wrote, and keeps them as a partial batch' -ForEach @(
        @{ Branch = 'LeRobot'; LeRobotInput = $true }
        @{ Branch = 'rollout'; LeRobotInput = $false }
    ) {
        $result = Invoke-JudgeEntry -LeRobotInput:$LeRobotInput -EnvVars @{
            JUDGE_BACKEND         = 'echo'
            JUDGE_INSTRUCTION_B64 = (ConvertTo-InstructionB64 'Keep the pole upright')
            FAKE_JUDGE_EXIT       = '1'
        }

        $result.ExitCode | Should -Not -Be 0
        $result.StdOut | Should -Match 'JUDGE_RESULT=episodes=1 successes=1'
        $result.StdErr | Should -Match 'could not judge every selected input \(exit 1\); the verdicts it wrote are in judgments\.partial\.jsonl'
        Test-Path -Path (Join-Path $result.WorkDir 'output/judgments.partial.jsonl') | Should -BeTrue
        Test-Path -Path (Join-Path $result.WorkDir 'output/judgments.jsonl') | Should -BeFalse
    }

    It 'fails and reports no verdicts when the judge fails before writing any' {
        $result = Invoke-JudgeEntry -LeRobotInput -EnvVars @{ FAKE_VERDICT = ''; FAKE_JUDGE_EXIT = '1' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'the judge wrote no verdicts'
        $result.StdErr | Should -Match 'could not judge every selected input \(exit 1\) and wrote no verdicts'
        Test-Path -Path (Join-Path $result.WorkDir 'output/judgments.jsonl') | Should -BeFalse
        Test-Path -Path (Join-Path $result.WorkDir 'output/judgments.partial.jsonl') | Should -BeFalse
    }
}
