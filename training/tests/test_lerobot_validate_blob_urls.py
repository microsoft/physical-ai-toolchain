"""Tests for training/il/scripts/lerobot/_validate_blob_urls.py."""

from __future__ import annotations

import pytest
from conftest import load_training_module

_VALIDATE = load_training_module(
    "training_il_scripts_lerobot_validate_blob_urls",
    "training/il/scripts/lerobot/_validate_blob_urls.py",
)


def _validate(*urls: str) -> None:
    _VALIDATE.validate(list(urls))


class TestValidateAccepts:
    def test_single_canonical_url(self):
        _validate("https://acct.blob.core.windows.net/cont/prefix")

    def test_multiple_canonical_urls(self):
        _validate(
            "https://a.blob.core.windows.net/c1/p1",
            "https://b.blob.core.windows.net/c2/p2/p3",
        )

    def test_exactly_max_urls(self):
        urls = [f"https://acct.blob.core.windows.net/c/p{i}" for i in range(_VALIDATE._MAX_BLOB_URLS)]
        _validate(*urls)


class TestValidateRejects:
    def test_empty_input(self):
        with pytest.raises(SystemExit, match="at least one Blob URL is required"):
            _validate()

    def test_too_many_urls(self):
        urls = [f"https://acct.blob.core.windows.net/c/p{i}" for i in range(_VALIDATE._MAX_BLOB_URLS + 1)]
        with pytest.raises(SystemExit, match="too many Blob URLs"):
            _VALIDATE.validate(urls)

    @pytest.mark.parametrize(
        "url",
        [
            "azureml:asset:1",
            "azureml://workspaces/x/datasets/y/versions/1",
            "AZUREML:asset:1",
        ],
    )
    def test_azureml_identifiers(self, url):
        with pytest.raises(SystemExit, match="does not accept AzureML asset identifiers"):
            _validate(url)

    @pytest.mark.parametrize(
        "url",
        [
            "wasbs://cont@acct.blob.core.windows.net/p",
            "abfss://cont@acct.dfs.core.windows.net/p",
        ],
    )
    def test_non_https_storage_schemes(self, url):
        with pytest.raises(SystemExit, match="supports HTTPS Azure Blob URLs only"):
            _validate(url)

    def test_http_scheme(self):
        with pytest.raises(SystemExit, match="must be an HTTPS Azure Blob URL"):
            _validate("http://acct.blob.core.windows.net/c/p")

    def test_empty_string(self):
        with pytest.raises(SystemExit, match="must be an HTTPS Azure Blob URL"):
            _validate("")

    def test_query_string(self):
        with pytest.raises(SystemExit, match="must not include a query string"):
            _validate("https://acct.blob.core.windows.net/c/p?sig=x")

    def test_fragment(self):
        with pytest.raises(SystemExit, match="must not include a fragment"):
            _validate("https://acct.blob.core.windows.net/c/p#frag")

    def test_explicit_port(self):
        with pytest.raises(SystemExit, match="must not include an explicit port"):
            _validate("https://acct.blob.core.windows.net:443/c/p")

    @pytest.mark.parametrize(
        "url",
        [
            "https://user:pass@acct.blob.core.windows.net/c/p",
            "https://user@acct.blob.core.windows.net/c/p",
            "https://:pass@acct.blob.core.windows.net/c/p",
            "https://@acct.blob.core.windows.net/c/p",
            "https://evil.com@acct.blob.core.windows.net/c/p",
        ],
    )
    def test_userinfo_in_netloc(self, url):
        with pytest.raises(SystemExit, match="must not include userinfo"):
            _validate(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://acct.blob.core.windows.net/c/p\n",
            "https://acct.blob.core.windows.net/c/p\rINJECTED",
            "https://acct.blob.core.windows.net/c/p\x00",
            "https://acct.blob.core.windows.net/c/\tp",
            "https://acct.blob.core.windows.net/c/p\x1b[31m",
            "https://acct.blob.core.windows.net/c/p\x7f",
        ],
    )
    def test_control_characters(self, url):
        with pytest.raises(SystemExit, match="must not contain control characters"):
            _validate(url)

    def test_single_quote_in_path(self):
        with pytest.raises(SystemExit, match="must not contain single-quote"):
            _validate("https://acct.blob.core.windows.net/c/it's-path")

    @pytest.mark.parametrize(
        "url",
        [
            "https://acct.file.core.windows.net/c/p",
            "https://acct.queue.core.windows.net/c/p",
            "https://acct.dfs.core.windows.net/c/p",
            "https://example.com/c/p",
        ],
    )
    def test_wrong_host_suffix(self, url):
        with pytest.raises(SystemExit, match="must target https://ACCOUNT"):
            _validate(url)

    def test_account_contains_dot(self):
        with pytest.raises(SystemExit, match="invalid storage account name"):
            _validate("https://acct.extra.blob.core.windows.net/c/p")

    def test_missing_container(self):
        with pytest.raises(SystemExit, match="must include a Blob container"):
            _validate("https://acct.blob.core.windows.net/")

    @pytest.mark.parametrize(
        "url",
        [
            "https://acct.blob.core.windows.net/c",
            "https://acct.blob.core.windows.net/c/",
            "https://acct.blob.core.windows.net/c//",
        ],
    )
    def test_missing_prefix(self, url):
        with pytest.raises(SystemExit, match="must include a non-empty Blob prefix"):
            _validate(url)

    def test_first_invalid_short_circuits(self):
        with pytest.raises(SystemExit, match="must be an HTTPS Azure Blob URL: http"):
            _validate("http://acct.blob.core.windows.net/c/p", "https://acct.blob.core.windows.net/c/p")


class TestModuleEntrypoint:
    def test_main_invocation_rejects_no_args(self, monkeypatch):
        monkeypatch.setattr(_VALIDATE.sys, "argv", ["_validate_blob_urls.py"])
        with pytest.raises(SystemExit, match="at least one Blob URL is required"):
            _VALIDATE.validate(_VALIDATE.sys.argv[1:])
