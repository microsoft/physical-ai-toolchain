"""Tests for scripts/security/check_hf_pins.py (HuggingFace revision-pin guard)."""

from __future__ import annotations

from pathlib import Path

from conftest import load_training_module

_MOD = load_training_module("scripts_security_check_hf_pins", "scripts/security/check_hf_pins.py")


class TestHasRevision:
    def test_repo_tree_is_clean(self) -> None:
        # The production tree must pass its own guard; a regression here means a
        # new bare from_pretrained/snapshot_download landed.
        assert _MOD.main(["check_hf_pins.py"]) == 0

    def test_flags_bare_from_pretrained(self, tmp_path: Path) -> None:
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text('X.from_pretrained("a/b")\n', encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 1

    def test_flags_bare_snapshot_download(self, tmp_path: Path) -> None:
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text('snapshot_download(repo_id="c")\n', encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 1

    def test_accepts_explicit_revision(self, tmp_path: Path) -> None:
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text('X.from_pretrained("a/b", revision=rev)\n', encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 0

    def test_accepts_kwargs_splat(self, tmp_path: Path) -> None:
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text("hf_hub_download(**opts)\n", encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 0

    def test_flags_literal_revision_none_or_empty(self, tmp_path: Path) -> None:
        # None, empty, and whitespace-only literals each resolve the mutable HEAD.
        for i, literal in enumerate(("None", '""', '"   "')):
            root = tmp_path / f"evaluation{i}"
            root.mkdir()
            (root / "mod.py").write_text(f'X.from_pretrained("a/b", revision={literal})\n', encoding="utf-8")

            assert _MOD.main(["check_hf_pins.py", str(root)]) == 1

    def test_flags_empty_fstring_revision(self, tmp_path: Path) -> None:
        # An f-string that folds to an empty literal resolves the mutable HEAD.
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text('X.from_pretrained("a/b", revision=f"")\n', encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 1

    def test_accepts_interpolated_fstring_revision(self, tmp_path: Path) -> None:
        # An interpolated f-string is a non-constant expression and is trusted.
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text('X.from_pretrained("a/b", revision=f"{sha}")\n', encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 0

    def test_flags_constant_fstring_branch_name(self, tmp_path: Path) -> None:
        # A fully constant f-string (no interpolation) folds to a literal ref; f"main"
        # is a branch name, not a SHA, so it must be flagged like a plain "main".
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text('X.from_pretrained("a/b", revision=f"main")\n', encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 1

    def test_accepts_constant_fstring_valid_sha(self, tmp_path: Path) -> None:
        # A fully constant f-string that folds to a 40-hex SHA is a valid immutable pin.
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text(
            'X.from_pretrained("a/b", revision=f"0123456789abcdef0123456789abcdef01234567")\n',
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 0

    def test_flags_non_string_constant_revision(self, tmp_path: Path) -> None:
        # A non-string constant is never a valid ref.
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text('X.from_pretrained("a/b", revision=0)\n', encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 1

    def test_accepts_valid_string_revision(self, tmp_path: Path) -> None:
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text(
            'X.from_pretrained("a/b", revision="0123456789abcdef0123456789abcdef01234567")\n', encoding="utf-8"
        )

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 0

    def test_flags_short_hex_revision(self, tmp_path: Path) -> None:
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text('X.from_pretrained("a/b", revision="abc1234")\n', encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 1

    def test_flags_branch_name_revision(self, tmp_path: Path) -> None:
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text('X.from_pretrained("a/b", revision="main")\n', encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 1

    def test_accepts_revision_variable_that_may_be_none(self, tmp_path: Path) -> None:
        # A non-constant revision expression is accepted; only literal None is rejected.
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text('X.from_pretrained("a/b", revision=rev_or_none)\n', encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 0

    def test_excludes_test_files(self, tmp_path: Path) -> None:
        root = tmp_path / "evaluation"
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_x.py").write_text('X.from_pretrained("a/b")\n', encoding="utf-8")
        (root / "test_top.py").write_text('X.from_pretrained("a/b")\n', encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 0

    def test_ignores_function_definitions(self, tmp_path: Path) -> None:
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text("def from_pretrained(cls, repo):\n    return repo\n", encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 0

    def test_fails_closed_on_syntax_error(self, tmp_path: Path, capsys) -> None:
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text("if True print('broken')\n", encoding="utf-8")

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 1
        assert "Files could not be scanned" in capsys.readouterr().err


class TestAliasResolution:
    def test_flags_aliased_import(self, tmp_path: Path) -> None:
        # ``import snapshot_download as dl`` then a bare ``dl(...)`` resolves the mutable HEAD.
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text(
            "from huggingface_hub import snapshot_download as dl\ndl(repo_id='x')\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 1

    def test_accepts_pinned_aliased_import(self, tmp_path: Path) -> None:
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text(
            "from huggingface_hub import snapshot_download as dl\ndl(repo_id='x', revision=rev)\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 0

    def test_flags_reassigned_name(self, tmp_path: Path) -> None:
        # A simple rebinding ``grab = snapshot_download`` must not launder away the pin.
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text(
            "from huggingface_hub import snapshot_download\ngrab = snapshot_download\ngrab(repo_id='x')\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 1

    def test_flags_chained_rebinding(self, tmp_path: Path) -> None:
        # Rebindings are resolved transitively to a fixpoint.
        root = tmp_path / "evaluation"
        root.mkdir()
        (root / "mod.py").write_text(
            "from huggingface_hub import snapshot_download as dl\nalias = dl\nalias(repo_id='x')\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 1


class TestYamlHeredocs:
    def test_flags_bare_call_in_workflow_heredoc(self, tmp_path: Path) -> None:
        # OSMO workflow YAML embeds real download calls in python heredocs.
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            "command: |\n            python3 << 'SCRIPT'\n"
            "            snapshot_download(repo_id='x')\n            SCRIPT\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1

    def test_accepts_pinned_call_in_workflow_heredoc(self, tmp_path: Path) -> None:
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            "command: |\n            python3 << 'SCRIPT'\n"
            "            snapshot_download(repo_id='x', revision=rev)\n            SCRIPT\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 0

    def test_ignores_yaml_outside_workflows(self, tmp_path: Path) -> None:
        root = tmp_path / "training"
        root.mkdir()
        (root / "config.yaml").write_text(
            "command: |\n            python3 << 'SCRIPT'\n"
            "            snapshot_download(repo_id='x')\n            SCRIPT\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 0

    def test_flags_dash_stdin_heredoc(self, tmp_path: Path) -> None:
        # ``python3 - <<'SCRIPT'`` is the explicit-stdin form; the guard must see it.
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            "command: |\n            python3 - <<'SCRIPT'\n"
            "            snapshot_download(repo_id='x')\n            SCRIPT\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1

    def test_flags_flagged_stdin_heredoc(self, tmp_path: Path) -> None:
        # Any interpreter flag (e.g. ``-u``) between python and ``<<`` must not hide the body.
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            "command: |\n            python -u <<SCRIPT\n"
            "            snapshot_download(repo_id='x')\n            SCRIPT\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1

    def test_flags_heredoc_with_trailing_redirect(self, tmp_path: Path) -> None:
        # A redirect/pipe after the delimiter must not hide the heredoc body.
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            "command: |\n            python3 << 'SCRIPT' > out.log 2>&1\n"
            "            snapshot_download(repo_id='x')\n            SCRIPT\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1

    def test_flags_backslash_continued_opener(self, tmp_path: Path) -> None:
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            "command: |\n            python3 \\\n<< 'SCRIPT'\n"
            "            snapshot_download(repo_id='x')\n            SCRIPT\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1

    def test_flags_run_python_opener(self, tmp_path: Path) -> None:
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            "command: |\n            run_python << 'SCRIPT'\n"
            "            snapshot_download(repo_id='x')\n            SCRIPT\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1

    def test_flags_env_var_opener(self, tmp_path: Path) -> None:
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            "command: |\n            $PYTHON << 'SCRIPT'\n"
            "            snapshot_download(repo_id='x')\n            SCRIPT\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1

    def test_flags_heredoc_written_to_py_file(self, tmp_path: Path) -> None:
        # ``cat > dl.py <<'PY' ... PY`` then ``python dl.py`` must not hide an unpinned call.
        root = tmp_path / "training"
        root.mkdir()
        (root / "entry.sh").write_text(
            "cat > /tmp/dl.py <<'PY'\nsnapshot_download(repo_id='x')\nPY\npython3 /tmp/dl.py\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 1

    def test_accepts_pinned_heredoc_written_to_py_file(self, tmp_path: Path) -> None:
        root = tmp_path / "training"
        root.mkdir()
        (root / "entry.sh").write_text(
            "cat > /tmp/dl.py <<'PY'\nsnapshot_download(repo_id='x', revision=rev)\nPY\npython3 /tmp/dl.py\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(root)]) == 0


class TestYamlInlineC:
    def test_flags_bare_call_in_inline_c(self, tmp_path: Path) -> None:
        # Workflow YAML also embeds guarded calls as ``python -c "..."`` one-liners.
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            'command: |\n            python -c "from huggingface_hub import snapshot_download; '
            "snapshot_download(repo_id='x')\"\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1

    def test_flags_version_suffixed_interpreter_inline_c(self, tmp_path: Path) -> None:
        # ``python3.12 -c`` must be scanned like ``python``/``python3``.
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            'command: |\n            python3.12 -c "from huggingface_hub import snapshot_download; '
            "snapshot_download(repo_id='x')\"\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1

    def test_accepts_pinned_inline_c(self, tmp_path: Path) -> None:
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            'command: |\n            python -c "import sys; from huggingface_hub import snapshot_download; '
            'snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2])" "$MODEL" "$SHA"\n',
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 0

    def test_flags_inline_c_after_escaped_quotes(self, tmp_path: Path) -> None:
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            'command: |\n            python -c "print(\\"start\\"); snapshot_download(repo_id=\'x\')"\n',
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1

    def test_flags_backslash_split_inline_c(self, tmp_path: Path) -> None:
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            "command: |\n            python3 \\\n"
            "              -c \"from huggingface_hub import snapshot_download; snapshot_download(repo_id='x')\"\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1

    def test_fails_closed_on_unparseable_inline_c_with_guarded_call(self, tmp_path: Path, capsys) -> None:
        # A guarded call in a payload that will not parse (e.g. shell ${VAR}) must fail closed.
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            'command: |\n            python -c "snapshot_download(repo_id=$MODEL)"\n',
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1
        assert "Files could not be scanned" in capsys.readouterr().err

    def test_fails_closed_on_multiline_shlex_error_with_guarded_call(self, tmp_path: Path, capsys) -> None:
        # If shlex.split fails due to unclosed quotes spanning physical lines, it must still fail closed.
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            "command: |\n            python -c \"\n            snapshot_download(repo_id='x')\n            \"\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 1
        assert "Files could not be scanned" in capsys.readouterr().err

    def test_skips_multiline_shlex_error_without_guarded_call(self, tmp_path: Path) -> None:
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            "command: |\n            echo \"\n            print('ok')\n            \"\n",
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 0

    def test_skips_unparseable_inline_c_without_guarded_call(self, tmp_path: Path) -> None:
        # An unrelated inline command with shell interpolation must not break CI.
        workflow = tmp_path / "training" / "workflows" / "osmo"
        workflow.mkdir(parents=True)
        (workflow / "train.yaml").write_text(
            'command: |\n            python -c "print($GREETING)"\n',
            encoding="utf-8",
        )

        assert _MOD.main(["check_hf_pins.py", str(tmp_path / "training")]) == 0
