---
description: 'Require canonical public package registries in dependency manifests and lockfiles for OSS reproducibility'
applyTo: '**/package.json,**/package-lock.json,**/npm-shrinkwrap.json,**/.npmrc,**/pyproject.toml,**/uv.lock,**/requirements*.txt'
---

# Public Dependency Feed Policy

This guidance is adapted from the [hve-core public dependency feed policy](https://github.com/microsoft/hve-core/blob/a146d8d01ad1e38f82d5d8eebb7fa8d1d88c4092/.github/instructions/dependency-feeds.instructions.md).

## Outcome

Every committed dependency manifest and lockfile resolves from a canonical public package registry so external contributors and CI can restore the repository without private network or feed access.

## Required Practice

* Generate npm lockfiles from `https://registry.npmjs.org/` and retain its canonical tarball URLs and integrity metadata.
* Keep every npm lockfile `integrity` value on `sha512`. Internal mirrors can omit `dist.integrity` and publish only `dist.shasum`, which makes npm record a weaker `sha1` value.
* Use public ecosystem sources for other package managers, including PyPI, the public PyTorch index, GitHub releases, NuGet Gallery, PowerShell Gallery, crates.io, and the Go module proxy.
* Run `npm run lint:public-dependency-feeds` after changing dependency metadata.
* When machine or enterprise configuration redirects a package manager through an internal mirror, override it with the canonical public registry while generating committed lockfiles.
* Treat a package version available only from an internal mirror as unpublished for this open-source repository.

## Prohibited Sources

Do not commit dependency source URLs pointing at private or organization-scoped artifact feeds, corporate or machine-level package proxies, authenticated URLs, or URLs carrying registry credentials. Do not commit lockfile entries whose `integrity` value uses an algorithm weaker than `sha512`.

## Restricted Networks

When a network blocks public registries, route installs through the approved proxy from the local environment and never from a tracked file.

* Restrict proxied use to restore commands such as `npm ci`, `uv sync --frozen`, and `pip install -r`.
* Do not resolve dependencies through a proxy. `npm install`, `npm update`, `npm audit fix`, `uv lock`, and `uv add` can write proxy URLs into lockfiles.
* Convert mirror-generated lockfiles in an isolated environment with direct access to the canonical public registry. Preserve package versions, dependency metadata, ordering, artifact filenames, and hashes.
* Do not hand-repair a proxy-generated lockfile. Public conversion must match package versions, filenames, and hashes against public registry metadata.
* Keep proxy addresses out of tracked files, including `.npmrc`, workflows, and devcontainer configuration.

## Stop Rule

If a required dependency is unavailable from an approved public registry, stop the dependency update and record the publication blocker. Do not substitute an internal mirror, private feed, unpublished archive, or credentialed source.

## Validation

`npm run lint:public-dependency-feeds` must pass before dependency changes are complete. PR and main validation enforce the same check.
