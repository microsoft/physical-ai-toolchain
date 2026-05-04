---
title: Changelog
description: Automatically generated changelog tracking all notable changes to the Azure NVIDIA Robotics Reference Architecture using semantic versioning
author: Edge AI Team
ms.date: 2026-02-06
ms.topic: reference
---

<!-- markdownlint-disable MD012 MD024 -->

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Note:** This file is automatically maintained by [release-please](https://github.com/googleapis/release-please). Do not edit manually.

## [0.8.0](https://github.com/microsoft/physical-ai-toolchain/compare/v0.7.4...v0.8.0) (2026-05-04)


### ⚠ BREAKING CHANGES

* **dataviewer:** bump frontend stack to React 19, Vite 8, Tailwind v4, MSAL 5, ESLint 10 ([#524](https://github.com/microsoft/physical-ai-toolchain/issues/524))

### ✨ Features

* **agents:** add automated validation for high-risk Dependabot bumps ([#574](https://github.com/microsoft/physical-ai-toolchain/issues/574)) ([8c3686a](https://github.com/microsoft/physical-ai-toolchain/commit/8c3686a355a2ebe1ef573160d9051814e298af8d)), closes [#573](https://github.com/microsoft/physical-ai-toolchain/issues/573)
* **data:** add camera selector to annotation workspace and fix AV1 frame extraction ([#591](https://github.com/microsoft/physical-ai-toolchain/issues/591)) ([c809d2f](https://github.com/microsoft/physical-ai-toolchain/commit/c809d2f203392f317d0cbd39ad8e7cb9622e74e8))
* **dataviewer:** add OWASP security middleware stack ([#439](https://github.com/microsoft/physical-ai-toolchain/issues/439)) ([239edb9](https://github.com/microsoft/physical-ai-toolchain/commit/239edb9b72fc1de3e09da86d3dae68856c096184))
* **infrastructure:** add conversion pipeline Terraform module ([#542](https://github.com/microsoft/physical-ai-toolchain/issues/542)) ([244531e](https://github.com/microsoft/physical-ai-toolchain/commit/244531e91b0f73231f29bf0e3d4978883650ebd6))
* **infrastructure:** upgrade OSMO to chart 1.2.1 / image 6.2 with secure auth and skrl 2.0.0 compatibility ([#492](https://github.com/microsoft/physical-ai-toolchain/issues/492)) ([edfd7a5](https://github.com/microsoft/physical-ai-toolchain/commit/edfd7a547f0341c70d5ac852e4799fa9b7aa8aa3))
* **pipeline:** add ACSA setup for ROS2 bag sync to Blob ([#451](https://github.com/microsoft/physical-ai-toolchain/issues/451)) ([c271a54](https://github.com/microsoft/physical-ai-toolchain/commit/c271a54e88a5308a793b346155f7360905d33771))
* **workflows:** add advisory Dependabot PR reviewer agentic workflow ([#498](https://github.com/microsoft/physical-ai-toolchain/issues/498)) ([d4bb140](https://github.com/microsoft/physical-ai-toolchain/commit/d4bb1408c86fbd2d930a162e39ece88af537ec85))
* **workflows:** trigger AW Dependabot PR reviewer after PR Validation ([#580](https://github.com/microsoft/physical-ai-toolchain/issues/580)) ([7ab3d16](https://github.com/microsoft/physical-ai-toolchain/commit/7ab3d1675fba94e1db0d43b35d585b2e777ca426))


### 🐛 Bug Fixes

* **ci:** correct stale version comment for actions/create-github-app-token ([#506](https://github.com/microsoft/physical-ai-toolchain/issues/506)) ([b2e9a54](https://github.com/microsoft/physical-ai-toolchain/commit/b2e9a54eccea8713a1bdd878690fda268ac44b09))
* **ci:** restore data-pipeline and training broken tests by domain folder restructure ([#547](https://github.com/microsoft/physical-ai-toolchain/issues/547)) ([06d8472](https://github.com/microsoft/physical-ai-toolchain/commit/06d847247e3c50ad072b32e966a3bcef55d064a3))
* **docs:** update remaining stale 'Coming soon' labels in docs/README.md ([#507](https://github.com/microsoft/physical-ai-toolchain/issues/507)) ([02439d6](https://github.com/microsoft/physical-ai-toolchain/commit/02439d6a8256da33bf21b495e85ed3fe8bae13f7))
* **docs:** update stale coming soon label for Training section ([#472](https://github.com/microsoft/physical-ai-toolchain/issues/472)) ([46db49b](https://github.com/microsoft/physical-ai-toolchain/commit/46db49bbf97800a037fdc35b17091147371c5995))
* **evaluation:** scope SIL AzureML validation code path and script reference ([#387](https://github.com/microsoft/physical-ai-toolchain/issues/387)) ([9f138a9](https://github.com/microsoft/physical-ai-toolchain/commit/9f138a96d37bf25bcc0af305a9c7dbbaec4733ce))
* **infrastructure:** OSMO workflow execution, PostgreSQL public access, and quickstart corrections ([#477](https://github.com/microsoft/physical-ai-toolchain/issues/477)) ([9ed2da6](https://github.com/microsoft/physical-ai-toolchain/commit/9ed2da6c1cece9316478ba97fb6e62cd53e65d9c))
* **workflows:** allow dependabot[bot] to activate AW Dependabot PR Review ([#586](https://github.com/microsoft/physical-ai-toolchain/issues/586)) ([39dc022](https://github.com/microsoft/physical-ai-toolchain/commit/39dc022d748848da8bea08ade454db01b7c316e5))
* **workflows:** correct branches filter on AW Dependabot PR Review workflow_run trigger ([#584](https://github.com/microsoft/physical-ai-toolchain/issues/584)) ([fe06b52](https://github.com/microsoft/physical-ai-toolchain/commit/fe06b52b4c3adbcb494f2cb17ea7d62f6c4607b4))
* **workflows:** normalize validate.yaml placeholder env/compute values ([#510](https://github.com/microsoft/physical-ai-toolchain/issues/510)) ([340ff44](https://github.com/microsoft/physical-ai-toolchain/commit/340ff44a3ff234f10aab9475f721eebd63e50285))
* **workflows:** recompile aw-dependabot-pr-review lock file ([#576](https://github.com/microsoft/physical-ai-toolchain/issues/576)) ([d77c167](https://github.com/microsoft/physical-ai-toolchain/commit/d77c16779ab73f20446185a7ce08218e738c8187))
* **workflows:** switch AW Dependabot PR Review to pull_request_target ([#589](https://github.com/microsoft/physical-ai-toolchain/issues/589)) ([3f1edd1](https://github.com/microsoft/physical-ai-toolchain/commit/3f1edd1b630d1c6d98e96cee5d9c6952858e8263))


### 📚 Documentation

* document dependency-pinning-artifacts directory purpose ([#508](https://github.com/microsoft/physical-ai-toolchain/issues/508)) ([50e0010](https://github.com/microsoft/physical-ai-toolchain/commit/50e0010acab8760401f9597d0ec7d193b523080b))


### 📦 Build System

* **training:** standardize on Python 3.12 across manifests, containers, and runtime scripts ([#541](https://github.com/microsoft/physical-ai-toolchain/issues/541)) ([7ad014a](https://github.com/microsoft/physical-ai-toolchain/commit/7ad014a0c31efd1b50d3818d6cfd8fe0cdded466))


### 🔧 Operations

* **build:** add Copilot cloud agent setup-steps workflow ([#593](https://github.com/microsoft/physical-ai-toolchain/issues/593)) ([c912668](https://github.com/microsoft/physical-ai-toolchain/commit/c912668e36b2947971f772170b4464f8164a0719))


### 🔧 Miscellaneous

* **build:** exclude auto-generated CHANGELOG.md from cspell and seed dictionary ([#582](https://github.com/microsoft/physical-ai-toolchain/issues/582)) ([de1dd57](https://github.com/microsoft/physical-ai-toolchain/commit/de1dd570d23bf888fa8648a33004b473071067b3))
* **build:** redesign codecov flags and split pytest CI per component ([#520](https://github.com/microsoft/physical-ai-toolchain/issues/520)) ([357e745](https://github.com/microsoft/physical-ai-toolchain/commit/357e745112fdb1db28704348dc95402528fd106d))
* **dataviewer:** bump frontend stack to React 19, Vite 8, Tailwind v4, MSAL 5, ESLint 10 ([#524](https://github.com/microsoft/physical-ai-toolchain/issues/524)) ([50f8ad4](https://github.com/microsoft/physical-ai-toolchain/commit/50f8ad41386a84c5b589c0c42124e49c59f379b5))
* **dataviewer:** repoint stale src/dataviewer references to data-management/viewer ([#504](https://github.com/microsoft/physical-ai-toolchain/issues/504)) ([88fa1b4](https://github.com/microsoft/physical-ai-toolchain/commit/88fa1b44e49006359e291e96b4f6a56963d0cc89)), closes [#503](https://github.com/microsoft/physical-ai-toolchain/issues/503)
* **deps-dev:** bump globals from 15.15.0 to 17.5.0 in /data-management/viewer/frontend ([#527](https://github.com/microsoft/physical-ai-toolchain/issues/527)) ([0e0b2ae](https://github.com/microsoft/physical-ai-toolchain/commit/0e0b2ae951219f6c71d788fc91b14439d050d858))
* **deps-dev:** bump the python-dependencies group across 1 directory with 2 updates ([#514](https://github.com/microsoft/physical-ai-toolchain/issues/514)) ([8410f4b](https://github.com/microsoft/physical-ai-toolchain/commit/8410f4bf6717c8344b62dfe238a6b556e3d26e7a))
* **deps:** bump azure-core from 1.39.0 to 1.40.0 in /evaluation in the inference-dependencies group across 1 directory ([#597](https://github.com/microsoft/physical-ai-toolchain/issues/597)) ([6141db4](https://github.com/microsoft/physical-ai-toolchain/commit/6141db4b1f9c1a995aa4bd3db031cb92d77e35a7))
* **deps:** bump cryptography from 46.0.6 to 46.0.7 in /data-management/viewer ([#424](https://github.com/microsoft/physical-ai-toolchain/issues/424)) ([5fb6d58](https://github.com/microsoft/physical-ai-toolchain/commit/5fb6d58d4b733de7def36087b9efc94afc11d739))
* **deps:** bump cryptography from 46.0.6 to 46.0.7 in /data-management/viewer/backend ([#423](https://github.com/microsoft/physical-ai-toolchain/issues/423)) ([b516ad5](https://github.com/microsoft/physical-ai-toolchain/commit/b516ad52a765634eac67b1e92ae9638343c33024))
* **deps:** bump lucide-react from 0.469.0 to 1.8.0 in /data-management/viewer/frontend ([#528](https://github.com/microsoft/physical-ai-toolchain/issues/528)) ([1bdfc1e](https://github.com/microsoft/physical-ai-toolchain/commit/1bdfc1e2c56c4666277f019aa61da65ada45f8bb))
* **deps:** bump nginx from `8aa63af` to `5616878` in /data-management/viewer/frontend ([#511](https://github.com/microsoft/physical-ai-toolchain/issues/511)) ([9e7e20e](https://github.com/microsoft/physical-ai-toolchain/commit/9e7e20e024ef7e68621ee9b1995755be3d3a3567))
* **deps:** bump nginx from 1.27-alpine to 1.29-alpine in /data-management/viewer/frontend ([#484](https://github.com/microsoft/physical-ai-toolchain/issues/484)) ([0e5c3dd](https://github.com/microsoft/physical-ai-toolchain/commit/0e5c3ddf5777713fdc336482a67e37139fafee88))
* **deps:** bump node from `435f353` to `e49fd70` in /data-management/viewer/frontend ([#560](https://github.com/microsoft/physical-ai-toolchain/issues/560)) ([2884649](https://github.com/microsoft/physical-ai-toolchain/commit/288464927e97ab39efa94e34b9d8e302e1476246))
* **deps:** bump react-is from 18.3.1 to 19.2.5 in /data-management/viewer/frontend ([#530](https://github.com/microsoft/physical-ai-toolchain/issues/530)) ([d51318c](https://github.com/microsoft/physical-ai-toolchain/commit/d51318c60cff823c5a237247b956c2e2b97fbe25))
* **deps:** bump tensordict from 0.11.0 to 0.12.1 in /evaluation in the inference-dependencies group across 1 directory ([#456](https://github.com/microsoft/physical-ai-toolchain/issues/456)) ([b24e733](https://github.com/microsoft/physical-ai-toolchain/commit/b24e733ccc8b2339a60eb1f3aa1f0fdf5f89de52))
* **deps:** bump the dataviewer-backend-dependencies group across 1 directory with 2 updates ([#531](https://github.com/microsoft/physical-ai-toolchain/issues/531)) ([171a1da](https://github.com/microsoft/physical-ai-toolchain/commit/171a1dad37e3af940671fd5b2431c1b8ef855216))
* **deps:** bump the dataviewer-backend-dependencies group across 1 directory with 5 updates ([#516](https://github.com/microsoft/physical-ai-toolchain/issues/516)) ([4f9a577](https://github.com/microsoft/physical-ai-toolchain/commit/4f9a577741276920bca4a897a57210b1ddfcf91f))
* **deps:** bump the dataviewer-dependencies group across 1 directory with 2 updates ([#529](https://github.com/microsoft/physical-ai-toolchain/issues/529)) ([8646971](https://github.com/microsoft/physical-ai-toolchain/commit/8646971529013ad95e56097e0849f671de423b7f))
* **deps:** bump the dataviewer-dependencies group across 1 directory with 3 updates ([#601](https://github.com/microsoft/physical-ai-toolchain/issues/601)) ([d28fb50](https://github.com/microsoft/physical-ai-toolchain/commit/d28fb508e05f85f4d8e15d24816d67cebb17dd9b))
* **deps:** bump the dataviewer-dependencies group across 1 directory with 5 updates ([#515](https://github.com/microsoft/physical-ai-toolchain/issues/515)) ([109ee81](https://github.com/microsoft/physical-ai-toolchain/commit/109ee81e12f03f6f2546e359570a9e7e399bc816))
* **deps:** bump the dataviewer-frontend-patch-minor group across 1 directory with 9 updates ([#563](https://github.com/microsoft/physical-ai-toolchain/issues/563)) ([c08f450](https://github.com/microsoft/physical-ai-toolchain/commit/c08f45046c9f51ab83c1183140382aacbcb071d9))
* **deps:** bump the github-actions group across 1 directory with 4 updates ([#459](https://github.com/microsoft/physical-ai-toolchain/issues/459)) ([2609c52](https://github.com/microsoft/physical-ai-toolchain/commit/2609c524ffd3f61e77c8a2679a1d8895c267d98e))
* **deps:** bump the github-actions group across 1 directory with 4 updates ([#517](https://github.com/microsoft/physical-ai-toolchain/issues/517)) ([f54bf5d](https://github.com/microsoft/physical-ai-toolchain/commit/f54bf5d678118a33fd834ad948a587bc3b027c81))
* **deps:** bump the inference-dependencies group across 1 directory with 11 updates ([#562](https://github.com/microsoft/physical-ai-toolchain/issues/562)) ([087f53a](https://github.com/microsoft/physical-ai-toolchain/commit/087f53a8f014ee4c875fb52a8d08aa941805e87f))
* **deps:** bump the pip group across 2 directories with 1 update ([#494](https://github.com/microsoft/physical-ai-toolchain/issues/494)) ([a14b6b0](https://github.com/microsoft/physical-ai-toolchain/commit/a14b6b0310d93b00a3de356bc1b0725aea2c01f2))
* **docs:** update stale Python 3.11 references to 3.12 ([#575](https://github.com/microsoft/physical-ai-toolchain/issues/575)) ([6f85c95](https://github.com/microsoft/physical-ai-toolchain/commit/6f85c95f3c9a94c68af9b387872438638fcc8e84))
* **scripts:** remove redundant SC1091 disables in OSMO deploy scripts ([#509](https://github.com/microsoft/physical-ai-toolchain/issues/509)) ([ae1cb82](https://github.com/microsoft/physical-ai-toolchain/commit/ae1cb82380391dcbf28e90d89ecb64d59faffb37))


### 🔒 Security

* **build:** pin dependencies and hash-verify downloads ([#465](https://github.com/microsoft/physical-ai-toolchain/issues/465)) ([0289f49](https://github.com/microsoft/physical-ai-toolchain/commit/0289f49cfb8dfa74b478713ef23c16f2d40776b4))
* **build:** remediate dependency security advisories ([#479](https://github.com/microsoft/physical-ai-toolchain/issues/479)) ([7196d6d](https://github.com/microsoft/physical-ai-toolchain/commit/7196d6d5548e653d8d6766efc1119dbed3bdcf5c))
* **deps-dev:** bump basic-ftp from 5.2.1 to 5.2.2 ([#454](https://github.com/microsoft/physical-ai-toolchain/issues/454)) ([cb158f1](https://github.com/microsoft/physical-ai-toolchain/commit/cb158f188ff459cce50edf933b851e871c89762d))
* **deps-dev:** bump basic-ftp from 5.2.2 to 5.3.0 ([#495](https://github.com/microsoft/physical-ai-toolchain/issues/495)) ([e983b8b](https://github.com/microsoft/physical-ai-toolchain/commit/e983b8b2e0aa168356f9835ca0b4413b7867eda9))
* **deps-dev:** bump hypothesis from 6.152.3 to 6.152.4 in the python-dependencies group ([#598](https://github.com/microsoft/physical-ai-toolchain/issues/598)) ([83384d2](https://github.com/microsoft/physical-ai-toolchain/commit/83384d2832ce9eadfed144c4d347ae9838171e00))
* **deps-dev:** bump markdownlint-cli2 from 0.22.0 to 0.22.1 in the root-npm-dependencies group ([#559](https://github.com/microsoft/physical-ai-toolchain/issues/559)) ([32bde35](https://github.com/microsoft/physical-ai-toolchain/commit/32bde35fd3fc19fda9773338855a2a4ac5e2a0b1))
* **deps-dev:** bump picomatch from 2.3.1 to 2.3.2 in /docs/docusaurus ([#455](https://github.com/microsoft/physical-ai-toolchain/issues/455)) ([66f86ca](https://github.com/microsoft/physical-ai-toolchain/commit/66f86cac49089de0678c108310a83e7765651864))
* **deps-dev:** bump postcss from 8.5.10 to 8.5.12 in /data-management/viewer/frontend ([#569](https://github.com/microsoft/physical-ai-toolchain/issues/569)) ([a652dba](https://github.com/microsoft/physical-ai-toolchain/commit/a652dbae9fb7b168384e7e396de7dc19caed1e45))
* **deps-dev:** bump the python-dependencies group with 2 updates ([#457](https://github.com/microsoft/physical-ai-toolchain/issues/457)) ([749d231](https://github.com/microsoft/physical-ai-toolchain/commit/749d2313f86f8fe7f04fd03cab4bf971de660aea))
* **deps-dev:** bump the python-dependencies group with 2 updates ([#485](https://github.com/microsoft/physical-ai-toolchain/issues/485)) ([71b44fd](https://github.com/microsoft/physical-ai-toolchain/commit/71b44fd42d69799d53d3a12ee6c24c974044a164))
* **deps-dev:** bump the python-dependencies group with 3 updates ([#564](https://github.com/microsoft/physical-ai-toolchain/issues/564)) ([9fc52fd](https://github.com/microsoft/physical-ai-toolchain/commit/9fc52fdb39cbbef0dfd6a242b936c3f6fef3dabe))
* **deps-dev:** bump typescript from 6.0.2 to 6.0.3 in /docs/docusaurus in the docusaurus-dependencies group ([#513](https://github.com/microsoft/physical-ai-toolchain/issues/513)) ([5694dbc](https://github.com/microsoft/physical-ai-toolchain/commit/5694dbcfd6836fb6be6b344aaf2283b17a099506))
* **deps:** bump azureml/openmpi4.1.0-ubuntu22.04 from 20260303.v5 to 20260409.v4 in /evaluation/sil/docker ([#480](https://github.com/microsoft/physical-ai-toolchain/issues/480)) ([25d4df8](https://github.com/microsoft/physical-ai-toolchain/commit/25d4df8e280d8d804377a5e9c771b87c0ab2f104))
* **deps:** bump cryptography from 46.0.6 to 46.0.7 in /evaluation in the uv group across 1 directory ([#538](https://github.com/microsoft/physical-ai-toolchain/issues/538)) ([92c5b2e](https://github.com/microsoft/physical-ai-toolchain/commit/92c5b2ec8af25bac857e009038b265697fc6942a))
* **deps:** bump follow-redirects from 1.15.11 to 1.16.0 in /docs/docusaurus ([#469](https://github.com/microsoft/physical-ai-toolchain/issues/469)) ([0458908](https://github.com/microsoft/physical-ai-toolchain/commit/0458908bc7ccc3f24c816e1a8c14792149cdf713))
* **deps:** bump node from 24.14.1-slim to 25.9.0-slim in /data-management/viewer/frontend ([#482](https://github.com/microsoft/physical-ai-toolchain/issues/482)) ([1532d09](https://github.com/microsoft/physical-ai-toolchain/commit/1532d095fbe5143ec9bb87a9a0b7f827b275aca6))
* **deps:** bump packaging from 26.0 to 26.1 in /evaluation in the inference-dependencies group ([#483](https://github.com/microsoft/physical-ai-toolchain/issues/483)) ([f4afb6c](https://github.com/microsoft/physical-ai-toolchain/commit/f4afb6ca32a7f1c67c940fa10236b2c43e39bb33))
* **deps:** bump pillow from 12.1.1 to 12.2.0 ([#467](https://github.com/microsoft/physical-ai-toolchain/issues/467)) ([39fb663](https://github.com/microsoft/physical-ai-toolchain/commit/39fb663869c155bac17b7785d11a71efa5324b32))
* **deps:** bump python from 3.11-slim to 3.14-slim in /data-management/viewer/backend ([#481](https://github.com/microsoft/physical-ai-toolchain/issues/481)) ([7af9dfc](https://github.com/microsoft/physical-ai-toolchain/commit/7af9dfc2903a4337a01ce6399c37ba26b971021e))
* **deps:** bump the dataviewer-backend-dependencies group across 1 directory with 15 updates ([#428](https://github.com/microsoft/physical-ai-toolchain/issues/428)) ([e4446a2](https://github.com/microsoft/physical-ai-toolchain/commit/e4446a23b40e9272146a007a3ade9f0a7f68694f))
* **deps:** bump the dataviewer-backend-dependencies group in /data-management/viewer/backend with 4 updates ([#487](https://github.com/microsoft/physical-ai-toolchain/issues/487)) ([0f57c5b](https://github.com/microsoft/physical-ai-toolchain/commit/0f57c5bd8619ecc1e34faa32a577faf409f0ce88))
* **deps:** bump the dataviewer-backend-dependencies group in /data-management/viewer/backend with 8 updates ([#566](https://github.com/microsoft/physical-ai-toolchain/issues/566)) ([d6e7869](https://github.com/microsoft/physical-ai-toolchain/commit/d6e78694638be898448594807a0a98717ed86232))
* **deps:** bump the dataviewer-dependencies group across 1 directory with 5 updates ([#464](https://github.com/microsoft/physical-ai-toolchain/issues/464)) ([24c208d](https://github.com/microsoft/physical-ai-toolchain/commit/24c208d6059f885744d60fa56db19edb47d1d747))
* **deps:** bump the dataviewer-dependencies group in /data-management/viewer with 2 updates ([#486](https://github.com/microsoft/physical-ai-toolchain/issues/486)) ([90149f3](https://github.com/microsoft/physical-ai-toolchain/commit/90149f34ffc2e87d6b8d777eb3265b83d79f9b27))
* **deps:** bump the dataviewer-dependencies group in /data-management/viewer with 6 updates ([#565](https://github.com/microsoft/physical-ai-toolchain/issues/565)) ([f0bb36b](https://github.com/microsoft/physical-ai-toolchain/commit/f0bb36b8549e136c991824cf581083edaa5f1af7))
* **deps:** bump the github-actions group across 1 directory with 4 updates ([#534](https://github.com/microsoft/physical-ai-toolchain/issues/534)) ([5478ab6](https://github.com/microsoft/physical-ai-toolchain/commit/5478ab6beafaf2c105dae64af870997551411df4))
* **deps:** bump the github-actions group with 2 updates ([#488](https://github.com/microsoft/physical-ai-toolchain/issues/488)) ([4e6ce98](https://github.com/microsoft/physical-ai-toolchain/commit/4e6ce9810a8e7cd9d4903994946f644e5f77fcb9))
* **deps:** bump the github-actions group with 3 updates ([#567](https://github.com/microsoft/physical-ai-toolchain/issues/567)) ([48c38dc](https://github.com/microsoft/physical-ai-toolchain/commit/48c38dcfffa1133a57f3fb66d85970b14d74959a))
* **deps:** bump the github-actions group with 6 updates ([#603](https://github.com/microsoft/physical-ai-toolchain/issues/603)) ([73eb79a](https://github.com/microsoft/physical-ai-toolchain/commit/73eb79ab241a371a5865a4f629305ead2610affc))
* **deps:** bump the training-dependencies group across 1 directory with 23 updates ([#463](https://github.com/microsoft/physical-ai-toolchain/issues/463)) ([d5a8656](https://github.com/microsoft/physical-ai-toolchain/commit/d5a86563ee3bba55d74469d184e0d54e0dcb193b))
* **deps:** bump yaml from 2.8.2 to 2.8.3 in /data-management/viewer/frontend ([#453](https://github.com/microsoft/physical-ai-toolchain/issues/453)) ([10449df](https://github.com/microsoft/physical-ai-toolchain/commit/10449df6acb72b79981fa4b320aab7271b07d100))
* pytest harness, dependabot advisories, and OSSF Scorecard remediations ([#501](https://github.com/microsoft/physical-ai-toolchain/issues/501)) ([e8756e8](https://github.com/microsoft/physical-ai-toolchain/commit/e8756e858ae36fd8389b3b19665aad8ae13d6cca))
* **scripts:** pin and hash-verify all shell script downloads ([#468](https://github.com/microsoft/physical-ai-toolchain/issues/468)) ([0c2bb9c](https://github.com/microsoft/physical-ai-toolchain/commit/0c2bb9cd79e88c56c77e5da5a2df6775ac2a6000))

## [0.7.4](https://github.com/microsoft/physical-ai-toolchain/compare/v0.7.3...v0.7.4) (2026-04-10)


### 🔧 Miscellaneous

* **deps:** bump cryptography from 46.0.6 to 46.0.7 in /training/rl ([#422](https://github.com/microsoft/physical-ai-toolchain/issues/422)) ([f220042](https://github.com/microsoft/physical-ai-toolchain/commit/f2200429daaac64fc850a18bf0b62ec09ff4c930))

## [0.7.3](https://github.com/microsoft/physical-ai-toolchain/compare/v0.7.2...v0.7.3) (2026-04-09)


### 🔒 Security

* **deps:** bump the training-dependencies group in /training/rl with 7 updates ([#408](https://github.com/microsoft/physical-ai-toolchain/issues/408)) ([7d980eb](https://github.com/microsoft/physical-ai-toolchain/commit/7d980eb52fe7dedd0e42a6e8da1a72c4760a943e))

## [0.7.2](https://github.com/microsoft/physical-ai-toolchain/compare/v0.7.1...v0.7.2) (2026-04-09)


### 🔒 Security

* **deps:** bump the dataviewer-dependencies group in /data-management/viewer with 13 updates ([#405](https://github.com/microsoft/physical-ai-toolchain/issues/405)) ([fb7b4a4](https://github.com/microsoft/physical-ai-toolchain/commit/fb7b4a4f0af826ab94c155e22921d55e94af469b))

## [0.7.1](https://github.com/microsoft/physical-ai-toolchain/compare/v0.7.0...v0.7.1) (2026-04-09)


### 🔒 Security

* **deps:** bump the docusaurus-dependencies group in /docs/docusaurus with 14 updates ([#404](https://github.com/microsoft/physical-ai-toolchain/issues/404)) ([ada7211](https://github.com/microsoft/physical-ai-toolchain/commit/ada721127ef0eb1cdfb5b3de6b74323ea9569cff))

## [0.7.0](https://github.com/microsoft/physical-ai-toolchain/compare/v0.6.1...v0.7.0) (2026-04-09)


### ✨ Features

* **build:** add hve-core release pipeline with dependency SBOM and signing artifacts ([#420](https://github.com/microsoft/physical-ai-toolchain/issues/420)) ([2ff839a](https://github.com/microsoft/physical-ai-toolchain/commit/2ff839a7583d0129a060316ecd3ef2fbcd9ef26d))
* **build:** enforce strict warnings across all linters ([#392](https://github.com/microsoft/physical-ai-toolchain/issues/392)) ([b75e217](https://github.com/microsoft/physical-ai-toolchain/commit/b75e21735ed655a1c9e027193c9b5939e2eb1c18))
* **evaluation:** add fuzz testing infrastructure and property-based tests ([#416](https://github.com/microsoft/physical-ai-toolchain/issues/416)) ([d97d42c](https://github.com/microsoft/physical-ai-toolchain/commit/d97d42cea662e825c1dd92bdbb1aa8bdb3521bc6))
* **infrastructure:** add optional ADLS Gen2 data lake storage account ([#398](https://github.com/microsoft/physical-ai-toolchain/issues/398)) ([3bb9012](https://github.com/microsoft/physical-ai-toolchain/commit/3bb9012be9d5004404064d7b0e9a1fda783c4981))
* **settings:** add HVE Core extension to workspace and devcontainer recommendations ([#226](https://github.com/microsoft/physical-ai-toolchain/issues/226)) ([f0735d8](https://github.com/microsoft/physical-ai-toolchain/commit/f0735d8e25a4a42dee9448351d6059609f10aef8))


### 🐛 Bug Fixes

* **docs:** fix broken links, harden Docusaurus config, and integrate CI workflow ([#430](https://github.com/microsoft/physical-ai-toolchain/issues/430)) ([ea99997](https://github.com/microsoft/physical-ai-toolchain/commit/ea9999735c8e20bb1420a540be2beb4ec48d7cbc))
* **scripts:** join shellcheck version output before -match to populate $Matches ([#432](https://github.com/microsoft/physical-ai-toolchain/issues/432)) ([8768e76](https://github.com/microsoft/physical-ai-toolchain/commit/8768e7635db46f772e38531a0535b1f7bcf117aa))
* **scripts:** map unmapped ShellCheck severity levels and harden version parsing ([#434](https://github.com/microsoft/physical-ai-toolchain/issues/434)) ([1e95a17](https://github.com/microsoft/physical-ai-toolchain/commit/1e95a17f7d03b8e4db752ee8758a95842918e5c0))
* **scripts:** resolve ShellCheck SC2034 and enable source-path resolution ([#443](https://github.com/microsoft/physical-ai-toolchain/issues/443)) ([04438ea](https://github.com/microsoft/physical-ai-toolchain/commit/04438ea5aee4c1cdd5e0ba74e12241935bb02387))


### 🔧 Miscellaneous

* **deps-dev:** bump basic-ftp from 5.2.0 to 5.2.1 ([#429](https://github.com/microsoft/physical-ai-toolchain/issues/429)) ([438660a](https://github.com/microsoft/physical-ai-toolchain/commit/438660acf1dd30f56c5ac81986daad9b1585f83d))
* **deps:** bump cryptography from 46.0.6 to 46.0.7 ([#425](https://github.com/microsoft/physical-ai-toolchain/issues/425)) ([2366647](https://github.com/microsoft/physical-ai-toolchain/commit/236664758681e6682cfa854c63619be3344058fb))

## [0.6.1](https://github.com/microsoft/physical-ai-toolchain/compare/v0.6.0...v0.6.1) (2026-04-08)


### 📦 Build System

* **build:** upgrade to Node 24 and bump npm devDependencies ([#414](https://github.com/microsoft/physical-ai-toolchain/issues/414)) ([e46ddcd](https://github.com/microsoft/physical-ai-toolchain/commit/e46ddcd8408de65166a2b0ab4e972cfa10556577))

## [0.6.0](https://github.com/microsoft/physical-ai-toolchain/compare/v0.5.0...v0.6.0) (2026-04-08)


### ✨ Features

* **build:** add terraform-docs generation pipeline ([#378](https://github.com/microsoft/physical-ai-toolchain/issues/378)) ([78e90d0](https://github.com/microsoft/physical-ai-toolchain/commit/78e90d07c4622e8cbaddb3d2696087775fafff6d))
* **infrastructure:** enable optional AML diagnostic logs ([#400](https://github.com/microsoft/physical-ai-toolchain/issues/400)) ([58dd8db](https://github.com/microsoft/physical-ai-toolchain/commit/58dd8dbcad4495b31ec4d1310d3663c7c36a03f0))
* **scripts:** consolidate scripts library paths and enhance dataviewer ([#383](https://github.com/microsoft/physical-ai-toolchain/issues/383)) ([176d9c9](https://github.com/microsoft/physical-ai-toolchain/commit/176d9c964264fff0765af741c5af113801a2a976))


### 🐛 Bug Fixes

* **build:** remediate CVEs, enforce equality pinning, repair Dependabot config ([#391](https://github.com/microsoft/physical-ai-toolchain/issues/391)) ([0c29148](https://github.com/microsoft/physical-ai-toolchain/commit/0c29148b99cc9547f2f70a24d13c9a07957999eb))
* **infrastructure:** add Storage File Data Privileged Contributor role for ML identity ([#380](https://github.com/microsoft/physical-ai-toolchain/issues/380)) ([378f7ed](https://github.com/microsoft/physical-ai-toolchain/commit/378f7ede40adcaf505c48200fa0539a3b402f452))
* **infrastructure:** replace hardcoded NAT Gateway availability zones with variable ([#356](https://github.com/microsoft/physical-ai-toolchain/issues/356)) ([a1397bd](https://github.com/microsoft/physical-ai-toolchain/commit/a1397bd3378fdddefb11396905b81233cf3f4ce5))
* **infrastructure:** resolve TFLint violations and enable hard-fail ([#376](https://github.com/microsoft/physical-ai-toolchain/issues/376)) ([dfb55cd](https://github.com/microsoft/physical-ai-toolchain/commit/dfb55cd6a0f7e0ec39080eae35e052500cabfc19))
* **scripts:** add dot-source guard to Invoke-MsDateFreshnessCheck.ps1 ([#397](https://github.com/microsoft/physical-ai-toolchain/issues/397)) ([f6f22c3](https://github.com/microsoft/physical-ai-toolchain/commit/f6f22c383583021fb4a694060eb5015f2eef777c))
* **training:** validate AzureML and OSMO RL submissions end to end ([#372](https://github.com/microsoft/physical-ai-toolchain/issues/372)) ([49904d3](https://github.com/microsoft/physical-ai-toolchain/commit/49904d3cb4ea36c635cb8de313e344845e6371cb))


### 📚 Documentation

* **infrastructure:** add terraform-docs tooling and improve developer experience ([#365](https://github.com/microsoft/physical-ai-toolchain/issues/365)) ([a0fb03a](https://github.com/microsoft/physical-ai-toolchain/commit/a0fb03abc3ecf6cc208719e99c93b260e83762d1))
* **reference:** centralize workflow template docs and convert workflow READMEs to pointer index ([#379](https://github.com/microsoft/physical-ai-toolchain/issues/379)) ([68097e4](https://github.com/microsoft/physical-ai-toolchain/commit/68097e4406dfa4fe77ae71d2b78a76580f3e61d0))


### 🔧 Miscellaneous

* **deps-dev:** bump the npm_and_yarn group across 1 directory with 2 updates ([#374](https://github.com/microsoft/physical-ai-toolchain/issues/374)) ([d848c8b](https://github.com/microsoft/physical-ai-toolchain/commit/d848c8bdc978275b020f3350d0b92f227d7795ee))
* **deps-dev:** bump vite from 6.4.1 to 6.4.2 in /data-management/viewer/frontend in the npm_and_yarn group across 1 directory ([#395](https://github.com/microsoft/physical-ai-toolchain/issues/395)) ([6ec7f19](https://github.com/microsoft/physical-ai-toolchain/commit/6ec7f1985534f85634d19e89240421b4441b796a))
* **deps:** bump the github-actions group across 1 directory with 7 updates ([#370](https://github.com/microsoft/physical-ai-toolchain/issues/370)) ([4d1b951](https://github.com/microsoft/physical-ai-toolchain/commit/4d1b9515ea389baf3ba06b54a7d9de6bea910cd5))
* **deps:** bump the uv group across 2 directories with 1 update ([#373](https://github.com/microsoft/physical-ai-toolchain/issues/373)) ([ba66ed9](https://github.com/microsoft/physical-ai-toolchain/commit/ba66ed9e272ba2872eb468d3228b333788173a4e))


### 🔒 Security

* **deps-dev:** bump brace-expansion from 1.1.12 to 1.1.13 in /docs/docusaurus in the npm_and_yarn group across 1 directory ([#389](https://github.com/microsoft/physical-ai-toolchain/issues/389)) ([27129d9](https://github.com/microsoft/physical-ai-toolchain/commit/27129d9ac3b0792eaa4eb11fb49c67bd8788f441))
* **deps-dev:** bump the npm_and_yarn group across 2 directories with 2 updates ([#363](https://github.com/microsoft/physical-ai-toolchain/issues/363)) ([aeae624](https://github.com/microsoft/physical-ai-toolchain/commit/aeae6245623a3bdd0cc9a2c8b8efc823a4ec9c0f))
* **deps-dev:** bump the python-dependencies group with 5 updates ([#403](https://github.com/microsoft/physical-ai-toolchain/issues/403)) ([bb85560](https://github.com/microsoft/physical-ai-toolchain/commit/bb85560ab0cea3f5b7178dc69a4b650fbfe79665))
* **deps:** bump cryptography from 46.0.5 to 46.0.6 in /training/rl ([#367](https://github.com/microsoft/physical-ai-toolchain/issues/367)) ([a82dd68](https://github.com/microsoft/physical-ai-toolchain/commit/a82dd687b89481d95a2d92eebe72a3c358c4a52c))
* **deps:** bump the inference-dependencies group in /evaluation with 2 updates ([#401](https://github.com/microsoft/physical-ai-toolchain/issues/401)) ([c88d253](https://github.com/microsoft/physical-ai-toolchain/commit/c88d253fa8dde725cbb75936243769d5f17720e0))
* **deps:** bump the pip group across 4 directories with 2 updates ([#411](https://github.com/microsoft/physical-ai-toolchain/issues/411)) ([1230fe0](https://github.com/microsoft/physical-ai-toolchain/commit/1230fe0891dd9bbe0833c9670bce93bbb7359c09))
* **deps:** bump the training-dependencies group across 1 directory with 67 updates ([#375](https://github.com/microsoft/physical-ai-toolchain/issues/375)) ([8e05172](https://github.com/microsoft/physical-ai-toolchain/commit/8e051726b1e89a328b831802c6d09fe5d250a2e6))
* **deps:** bump the uv group across 2 directories with 1 update ([#382](https://github.com/microsoft/physical-ai-toolchain/issues/382)) ([b6c7aea](https://github.com/microsoft/physical-ai-toolchain/commit/b6c7aea8df3e8adbfc56474d452126df434f5daf))
* **deps:** update marshmallow requirement from &lt;4.3.0,&gt;=3.5 to &gt;=3.5,&lt;4.4.0 in /evaluation in the inference-dependencies group ([#393](https://github.com/microsoft/physical-ai-toolchain/issues/393)) ([599c7eb](https://github.com/microsoft/physical-ai-toolchain/commit/599c7eb7d3549fece16c92d55853765295d93092))

## [0.5.0](https://github.com/microsoft/physical-ai-toolchain/compare/v0.4.0...v0.5.0) (2026-03-26)


### ✨ Features

* add dataviewer web application for dataset analysis and annotation ([#375](https://github.com/microsoft/physical-ai-toolchain/issues/375)) ([c44d7bb](https://github.com/microsoft/physical-ai-toolchain/commit/c44d7bbe7765e456e75c2ccba969eef1d116f912))
* add return type annotations to cli_args functions ([#476](https://github.com/microsoft/physical-ai-toolchain/issues/476)) ([35523ee](https://github.com/microsoft/physical-ai-toolchain/commit/35523ee5fb16a1eb6916aa154524c5798ad1477b))
* add YAML config schema with pydantic validation for ROS 2 recording ([#376](https://github.com/microsoft/physical-ai-toolchain/issues/376)) ([1fa5243](https://github.com/microsoft/physical-ai-toolchain/commit/1fa52430274c04813f207306810b8e0df0fe9994))
* **agents:** Copilot agents and skills for dataviewer and OSMO training workflows. ([#444](https://github.com/microsoft/physical-ai-toolchain/issues/444)) ([8b72daf](https://github.com/microsoft/physical-ai-toolchain/commit/8b72dafd3a9b23be4bed7c4d97703fc289d713b2))
* **build:** add automated ms.date freshness checking ([#448](https://github.com/microsoft/physical-ai-toolchain/issues/448)) ([f92ddbc](https://github.com/microsoft/physical-ai-toolchain/commit/f92ddbcb27027ed6cc4b183d2361d6b8b39e42f7))
* **build:** add CLA section, Dependabot security prefix, and OWASP ZAP DAST scan ([#241](https://github.com/microsoft/physical-ai-toolchain/issues/241)) ([083a8af](https://github.com/microsoft/physical-ai-toolchain/commit/083a8af41ec859e76512fa43da3cba54f9311abd))
* **build:** add coverage.py configuration to pyproject.toml ([#428](https://github.com/microsoft/physical-ai-toolchain/issues/428)) ([eac7426](https://github.com/microsoft/physical-ai-toolchain/commit/eac74261d10a887bf834963bf593b92ff5aeb427))
* **build:** add Go CI pipeline with golangci-lint and go test ([#351](https://github.com/microsoft/physical-ai-toolchain/issues/351)) ([b27e4fb](https://github.com/microsoft/physical-ai-toolchain/commit/b27e4fb2a8d4a9144fa263f382d67e3580d75f5f))
* **build:** add OpenSSF Scorecard workflow and badge ([#431](https://github.com/microsoft/physical-ai-toolchain/issues/431)) ([98a62e7](https://github.com/microsoft/physical-ai-toolchain/commit/98a62e7b089008d1401661498ac96057e0983584))
* **build:** add release artifact signing and SBOM attestation ([#480](https://github.com/microsoft/physical-ai-toolchain/issues/480)) ([b226e96](https://github.com/microsoft/physical-ai-toolchain/commit/b226e964e56fa7d27fd308d2a9ca8157e0e9ad35))
* **build:** add TFLint reusable GitHub Actions workflow ([#229](https://github.com/microsoft/physical-ai-toolchain/issues/229)) ([34d5575](https://github.com/microsoft/physical-ai-toolchain/commit/34d5575acafd0b304e97e3af0665659821ce4455))
* **build:** split Go CI into separate lint and test pipelines ([#354](https://github.com/microsoft/physical-ai-toolchain/issues/354)) ([2dec155](https://github.com/microsoft/physical-ai-toolchain/commit/2dec15508706df6248f5aee5458d44c9383392f7))
* **dataviewer:** add authentication middleware and CSRF protection for mutation endpoints ([#432](https://github.com/microsoft/physical-ai-toolchain/issues/432)) ([77c8a01](https://github.com/microsoft/physical-ai-toolchain/commit/77c8a016a6462e8649273967f6a3abaa63bcf5fc))
* **docs:** create training documentation hub with guides and migration ([#380](https://github.com/microsoft/physical-ai-toolchain/issues/380)) ([0fdccc5](https://github.com/microsoft/physical-ai-toolchain/commit/0fdccc51b31e3d9e1b9b1390d3775ee3a329b601))
* **docs:** port Docusaurus documentation site with full build validation ([#182](https://github.com/microsoft/physical-ai-toolchain/issues/182)) ([29dd640](https://github.com/microsoft/physical-ai-toolchain/commit/29dd6405c6d41ccfc66ec1f85a111ad511c498ce))
* fix and deploy dataviewer ([#498](https://github.com/microsoft/physical-ai-toolchain/issues/498)) ([c922d49](https://github.com/microsoft/physical-ai-toolchain/commit/c922d49108c5a7603fb7d4b10eab45d1bf7b562e))
* **inference:** add AzureML and local LeRobot inference workflows ([#438](https://github.com/microsoft/physical-ai-toolchain/issues/438)) ([f7d786a](https://github.com/microsoft/physical-ai-toolchain/commit/f7d786a9559cddcea449a47cf4c7512372b070ca))
* **inference:** add MLflow trajectory plots and multi-source support to OSMO inference workflow ([#421](https://github.com/microsoft/physical-ai-toolchain/issues/421)) ([8637458](https://github.com/microsoft/physical-ai-toolchain/commit/8637458cf495a54dd44e95dea1fd392abb83efd1))
* **infra:** add blob storage lifecycle policies and folder structure ([#179](https://github.com/microsoft/physical-ai-toolchain/issues/179)) ([101a6e8](https://github.com/microsoft/physical-ai-toolchain/commit/101a6e889d80b941524c13173d8e19523a880d19))
* **infrastructure:** add optional observability and compute feature flags ([#437](https://github.com/microsoft/physical-ai-toolchain/issues/437)) ([9eba0da](https://github.com/microsoft/physical-ai-toolchain/commit/9eba0dac14784ddbbe7ab0bb8777655e87e7ef1f))
* **infrastructure:** add private Linux Isaac Sim VM deployment option ([#348](https://github.com/microsoft/physical-ai-toolchain/issues/348)) ([3748c2d](https://github.com/microsoft/physical-ai-toolchain/commit/3748c2d1db79210f04da59bf9c9584e699ad1159))
* **infrastructure:** add terraform-docs auto-generation pipeline ([#358](https://github.com/microsoft/physical-ai-toolchain/issues/358)) ([6565caa](https://github.com/microsoft/physical-ai-toolchain/commit/6565caa2e704c7ef55244b8d6e2eba2d66847d6e))
* **infrastructure:** harden Isaac Sim VM deployment with encryption and spot options ([#355](https://github.com/microsoft/physical-ai-toolchain/issues/355)) ([6ebc1f2](https://github.com/microsoft/physical-ai-toolchain/commit/6ebc1f2f2aba5fc7c5ab75228f1471a4c000d595))
* **repo:** migrate to domain-driven architecture ([#270](https://github.com/microsoft/physical-ai-toolchain/issues/270)) ([a339e70](https://github.com/microsoft/physical-ai-toolchain/commit/a339e706f7dc4b0d38aaea7e39c0649df820e82b))
* **scripts:** add --config-preview and deployment summary to submission scripts ([#499](https://github.com/microsoft/physical-ai-toolchain/issues/499)) ([4069806](https://github.com/microsoft/physical-ai-toolchain/commit/4069806c8a97993163aa6411fe036ee74666702a))
* **scripts:** add Copilot attribution footer validation to frontmatter linting ([#378](https://github.com/microsoft/physical-ai-toolchain/issues/378)) ([4d595f2](https://github.com/microsoft/physical-ai-toolchain/commit/4d595f258331c213539e90bf288e9c739788a68e))
* **src:** add dataviewer web application with storage adapter layer ([#404](https://github.com/microsoft/physical-ai-toolchain/issues/404)) ([8a9fb70](https://github.com/microsoft/physical-ai-toolchain/commit/8a9fb7009d16e6f7a579ab75662aabdf0e0810ca))


### 🐛 Bug Fixes

* **build:** add GHSA to cspell custom dictionary ([#315](https://github.com/microsoft/physical-ai-toolchain/issues/315)) ([67db81a](https://github.com/microsoft/physical-ai-toolchain/commit/67db81a1ed24a7039d908246e7cb8a2d3c6a576d))
* **build:** correct codecov report_type input for terraform test uploads ([#324](https://github.com/microsoft/physical-ai-toolchain/issues/324)) ([d90d66d](https://github.com/microsoft/physical-ai-toolchain/commit/d90d66d70e42a21692f6b0f7c3155a8fc258f787))
* **build:** expand CODEOWNERS coverage to critical paths ([#505](https://github.com/microsoft/physical-ai-toolchain/issues/505)) ([bafade1](https://github.com/microsoft/physical-ai-toolchain/commit/bafade1012ccde9b3d4a9613884d39e98ef0e2d6))
* **build:** pin Docker base image and pip dependencies with Dependabot coverage ([#497](https://github.com/microsoft/physical-ai-toolchain/issues/497)) ([d3d7ea4](https://github.com/microsoft/physical-ai-toolchain/commit/d3d7ea4a507fd0f747c6eccdb1a1f1db4c4f4e45))
* **build:** pin pydantic version and use uv in config schema validation workflow ([#493](https://github.com/microsoft/physical-ai-toolchain/issues/493)) ([28d823f](https://github.com/microsoft/physical-ai-toolchain/commit/28d823ff2a8f56d29b9800bcfea7cb497eb2523a))
* **build:** pin uv installer to versioned URL ([#495](https://github.com/microsoft/physical-ai-toolchain/issues/495)) ([8d8541b](https://github.com/microsoft/physical-ai-toolchain/commit/8d8541ba736d0478eb0e13637fbbaa0e0e24f06a))
* **build:** remediate GHSA vulnerabilities flagged by OSSF Scorecard ([#271](https://github.com/microsoft/physical-ai-toolchain/issues/271)) ([49b6e58](https://github.com/microsoft/physical-ai-toolchain/commit/49b6e58776233ebdeeb213065e2cc5f2f51e59be))
* **build:** remove README frontmatter, add FrontmatterExcludePaths, enforce Pester 5 ([#443](https://github.com/microsoft/physical-ai-toolchain/issues/443)) ([641d0f3](https://github.com/microsoft/physical-ai-toolchain/commit/641d0f37cb12a7aeee9d3d1bf9bd8cd9ee5e1188))
* **build:** resolve CI failures for release 0.5.0 PR ([#174](https://github.com/microsoft/physical-ai-toolchain/issues/174)) ([62c9900](https://github.com/microsoft/physical-ai-toolchain/commit/62c9900a99aa8e3dae9c310152bdce6557d9ba52))
* **build:** resolve codecov PR comment suppression ([#523](https://github.com/microsoft/physical-ai-toolchain/issues/523)) ([5603bd7](https://github.com/microsoft/physical-ai-toolchain/commit/5603bd73f8c572ffac7d5c078798dfb9ad5cb36a))
* **build:** use npm ci for deterministic frontend dependency install ([#491](https://github.com/microsoft/physical-ai-toolchain/issues/491)) ([ee8b5d3](https://github.com/microsoft/physical-ai-toolchain/commit/ee8b5d3219d35f1fddb357e4ddad7a1489bfe37a)), closes [#490](https://github.com/microsoft/physical-ai-toolchain/issues/490)
* **ci:** add `wait_for_ci` to Codecov configuration ([#183](https://github.com/microsoft/physical-ai-toolchain/issues/183)) ([370cf44](https://github.com/microsoft/physical-ai-toolchain/commit/370cf442a21f0463d0db484db5c253a2695f847a))
* **CI:** Issue 116 clean up dataviewer tests ([#184](https://github.com/microsoft/physical-ai-toolchain/issues/184)) ([f466c23](https://github.com/microsoft/physical-ai-toolchain/commit/f466c23e3db9db65163591ac7902a4011d212333))
* **ci:** pin pydantic to ==2.12.5 across all references ([#230](https://github.com/microsoft/physical-ai-toolchain/issues/230)) ([9d841d5](https://github.com/microsoft/physical-ai-toolchain/commit/9d841d57f488f4c794cdfa706f1d034d2c9d4cdd))
* **dataviewer:** add HTTP Range support for blob video streaming ([#165](https://github.com/microsoft/physical-ai-toolchain/issues/165)) ([8adde50](https://github.com/microsoft/physical-ai-toolchain/commit/8adde501f6440f8c0ddfdc0bba2cfb82312b8713))
* **dataviewer:** remediate CodeQL alerts and align ruff config ([#419](https://github.com/microsoft/physical-ai-toolchain/issues/419)) ([eb6fac9](https://github.com/microsoft/physical-ai-toolchain/commit/eb6fac9a1ddae1288cfc5ea3fdf612fbe0f846bd))
* **dataviewer:** remediate path traversal and input validation vulnerabilities ([#413](https://github.com/microsoft/physical-ai-toolchain/issues/413)) ([0a1d2ca](https://github.com/microsoft/physical-ai-toolchain/commit/0a1d2caef9a322721f0a4ea5dd8a30457231397b))
* **docs:** remove trailingSlash: false for GitHub Pages compatibility ([#228](https://github.com/microsoft/physical-ai-toolchain/issues/228)) ([a78cb97](https://github.com/microsoft/physical-ai-toolchain/commit/a78cb97510ff4edb6669d05db09bfd215db96045))
* **gpu:** add GPU Operator validation dependencies to GRID driver installer ([#441](https://github.com/microsoft/physical-ai-toolchain/issues/441)) ([eec42da](https://github.com/microsoft/physical-ai-toolchain/commit/eec42da514aaecab8ef25ba3cf46ea7cd31ecc29))
* **infrastructure:** add zone-redundant config to VPN gateway public IP ([#352](https://github.com/microsoft/physical-ai-toolchain/issues/352)) ([2d734f4](https://github.com/microsoft/physical-ai-toolchain/commit/2d734f4ea17031407104e6cb6744c5fc1038bb93))
* **infrastructure:** improve stdout handling for helm commands in GPU… ([#311](https://github.com/microsoft/physical-ai-toolchain/issues/311)) ([153f467](https://github.com/microsoft/physical-ai-toolchain/commit/153f467239d3b746e82c872bb933497b6be9bee2))
* **infrastructure:** resolve remaining TFLint violations in SIL module and example configs ([#298](https://github.com/microsoft/physical-ai-toolchain/issues/298)) ([c0ce3e5](https://github.com/microsoft/physical-ai-toolchain/commit/c0ce3e5aa0f554b28c6deb8f634547c6a9d34137))
* **infrastructure:** resolve TFLint violations in root and automation modules ([#287](https://github.com/microsoft/physical-ai-toolchain/issues/287)) ([b6a4604](https://github.com/microsoft/physical-ai-toolchain/commit/b6a4604cb42b87808014e8799eab8a1b0bd5a8e3)), closes [#203](https://github.com/microsoft/physical-ai-toolchain/issues/203)
* **infrastructure:** update deprecated bgp vng variable name ([#307](https://github.com/microsoft/physical-ai-toolchain/issues/307)) ([f530734](https://github.com/microsoft/physical-ai-toolchain/commit/f5307346fe741668bf41d8d9532c23398fbdbd9b))
* **scripts:** pin uv version in OSMO workflow templates ([#500](https://github.com/microsoft/physical-ai-toolchain/issues/500)) ([7edf13a](https://github.com/microsoft/physical-ai-toolchain/commit/7edf13a7785f590fb9332f8ed0acc24445a70360))
* **scripts:** replace lambda with def in lerobot_handler to satisfy R… ([#176](https://github.com/microsoft/physical-ai-toolchain/issues/176)) ([baf9e58](https://github.com/microsoft/physical-ai-toolchain/commit/baf9e58961044fb5acdcb987be681e2bfd195977))
* **scripts:** support OSMO control-plane deploys with in-cluster Redis ([#317](https://github.com/microsoft/physical-ai-toolchain/issues/317)) ([d4b70de](https://github.com/microsoft/physical-ai-toolchain/commit/d4b70defe6191ef430fd0d40be871099b8488db0))
* **scripts:** update compute target name derivation logic ([#319](https://github.com/microsoft/physical-ai-toolchain/issues/319)) ([bb20431](https://github.com/microsoft/physical-ai-toolchain/commit/bb20431a1169a7bc40c29ffdba3f9424aff5cb0f))
* **settings:** update devcontainer name to match project context ([#177](https://github.com/microsoft/physical-ai-toolchain/issues/177)) ([745321e](https://github.com/microsoft/physical-ai-toolchain/commit/745321e7e62df0f0dde6b904311af279637fcd80))
* **terraform:** create PostgreSQL Key Vault secret via ARM control plane ([#304](https://github.com/microsoft/physical-ai-toolchain/issues/304)) ([5d73b81](https://github.com/microsoft/physical-ai-toolchain/commit/5d73b817792dbd7e7dfda5977480eaea3947907a))
* **terraform:** gate observability with feature flags ([#303](https://github.com/microsoft/physical-ai-toolchain/issues/303)) ([ea5e056](https://github.com/microsoft/physical-ai-toolchain/commit/ea5e05609c8283ea83585bff25758bb94d117831))
* **terraform:** switch VPN gateway defaults to AZ SKUs ([#309](https://github.com/microsoft/physical-ai-toolchain/issues/309)) ([74989c5](https://github.com/microsoft/physical-ai-toolchain/commit/74989c5048e810cd8a5cbd9b6cfb2182c97067b7))
* **training:** correct learning rate mapping and pin LeRobot version ([#439](https://github.com/microsoft/physical-ai-toolchain/issues/439)) ([5cf9943](https://github.com/microsoft/physical-ai-toolchain/commit/5cf9943390b62a1edc6ae923e6f820bc6c40529b))
* **workflows:** enable SARIF upload for dependency-pinning scans ([#502](https://github.com/microsoft/physical-ai-toolchain/issues/502)) ([124cad6](https://github.com/microsoft/physical-ai-toolchain/commit/124cad65806838398b4fcfc72bedf9c376153061)), closes [#501](https://github.com/microsoft/physical-ai-toolchain/issues/501)
* **workflows:** remove redundant top-level permissions from codeql-analysis ([#489](https://github.com/microsoft/physical-ai-toolchain/issues/489)) ([1490fda](https://github.com/microsoft/physical-ai-toolchain/commit/1490fda6c522eacdba74bde6904fb2b7627a87ce))
* **workflows:** use bash shell for uv.lock regeneration and add SARIF to dictionary ([#225](https://github.com/microsoft/physical-ai-toolchain/issues/225)) ([e6fa6ea](https://github.com/microsoft/physical-ai-toolchain/commit/e6fa6ea2f0d883492942011b770e03ce65c5416d))


### 📚 Documentation

* add chunking and compression configuration guide for Jetson edge recording ([#408](https://github.com/microsoft/physical-ai-toolchain/issues/408)) ([787a322](https://github.com/microsoft/physical-ai-toolchain/commit/787a322b4776b0d4159212ed22e25ecd326653e4))
* add OpenSSF Best Practices badge to README ([#282](https://github.com/microsoft/physical-ai-toolchain/issues/282)) ([01ea384](https://github.com/microsoft/physical-ai-toolchain/commit/01ea384859f3093cba91fb08e71884fae453ac73))
* add threat model cross-reference to SECURITY.md ([#235](https://github.com/microsoft/physical-ai-toolchain/issues/235)) ([88a461e](https://github.com/microsoft/physical-ai-toolchain/commit/88a461ec348a88cee09e845e706d60d4cbde8878))
* add vulnerability remediation timeline to SECURITY.md ([#233](https://github.com/microsoft/physical-ai-toolchain/issues/233)) ([5ead3ee](https://github.com/microsoft/physical-ai-toolchain/commit/5ead3ee137ca8982ec391d5a12cac8ec0a731ef9))
* **contributing:** remove version-specific planning language from ownership tip ([#407](https://github.com/microsoft/physical-ai-toolchain/issues/407)) ([3191f9b](https://github.com/microsoft/physical-ai-toolchain/commit/3191f9be34a0b2e4b81209aa6daa3de0a10d6d27))
* **deploy:** replace deploy/ READMEs with pointer files ([#379](https://github.com/microsoft/physical-ai-toolchain/issues/379)) ([b3c3abb](https://github.com/microsoft/physical-ai-toolchain/commit/b3c3abb2ea3a6def816f7227ad9e7fef40e891f0))
* **docs:** add bug report response timeline for OSSF report_responses criterion ([#485](https://github.com/microsoft/physical-ai-toolchain/issues/485)) ([9b26212](https://github.com/microsoft/physical-ai-toolchain/commit/9b2621273616114d52bbffcda8a49b07500805f4))
* **docs:** add component update process for OpenSSF Silver badge ([#446](https://github.com/microsoft/physical-ai-toolchain/issues/446)) ([6adc8a2](https://github.com/microsoft/physical-ai-toolchain/commit/6adc8a27ba2a1cbb430a70deeb579201e1b12969))
* **docs:** Add data collection and training recipes ([#343](https://github.com/microsoft/physical-ai-toolchain/issues/343)) ([9c34f86](https://github.com/microsoft/physical-ai-toolchain/commit/9c34f8683b2f30c7596f5c4bf365a7ba0694f921))
* **docs:** add deprecation policy for external interfaces ([#445](https://github.com/microsoft/physical-ai-toolchain/issues/445)) ([229d5db](https://github.com/microsoft/physical-ai-toolchain/commit/229d5db57f3f64e40b470d523ec2c81e3aa9b206))
* **docs:** add structure for recipes in repo ([#322](https://github.com/microsoft/physical-ai-toolchain/issues/322)) ([098757b](https://github.com/microsoft/physical-ai-toolchain/commit/098757b196f8a6b8dadeaed4b6bbd8416d07833d))
* **docs:** add YAML frontmatter to SUPPORT.md ([#478](https://github.com/microsoft/physical-ai-toolchain/issues/478)) ([d94c15d](https://github.com/microsoft/physical-ai-toolchain/commit/d94c15df31afe4ef998c19f646d8e4f8f73ad720)), closes [#347](https://github.com/microsoft/physical-ai-toolchain/issues/347)
* **docs:** clarify issue assignment requirement before starting work ([#299](https://github.com/microsoft/physical-ai-toolchain/issues/299)) ([1534462](https://github.com/microsoft/physical-ai-toolchain/commit/15344620ef695f32f3c624b0f20f5399313c1437))
* **docs:** create inference and training docs hubs ([#402](https://github.com/microsoft/physical-ai-toolchain/issues/402)) ([7a20a2e](https://github.com/microsoft/physical-ai-toolchain/commit/7a20a2ec570bc257ec8fbe86e857f675894aa8b5))
* **docs:** create reference hub and migrate script documentation ([#503](https://github.com/microsoft/physical-ai-toolchain/issues/503)) ([03a31c6](https://github.com/microsoft/physical-ai-toolchain/commit/03a31c622c588c8b1b41ed2b0b6e9014645ab90f))
* **docs:** create training and inference documentation hubs ([#403](https://github.com/microsoft/physical-ai-toolchain/issues/403)) ([7be003b](https://github.com/microsoft/physical-ai-toolchain/commit/7be003bccd97b0d88d70f8f3462e9d34c1e48234))
* **operations:** create operations hub and troubleshooting guide ([#525](https://github.com/microsoft/physical-ai-toolchain/issues/525)) ([31c7aaa](https://github.com/microsoft/physical-ai-toolchain/commit/31c7aaae4004b7b9a357fc4460e19b2b97a1347b))
* **reference:** add copilot artifacts documentation hub ([#170](https://github.com/microsoft/physical-ai-toolchain/issues/170)) ([9a45ca4](https://github.com/microsoft/physical-ai-toolchain/commit/9a45ca49c1f11eace5445e489144c6b24d039955))
* simplify root README and update prerequisites ([#440](https://github.com/microsoft/physical-ai-toolchain/issues/440)) ([c0c7710](https://github.com/microsoft/physical-ai-toolchain/commit/c0c77107d67167e7ba3a7767722d0409dfc6ea3f))


### ♻️ Code Refactoring

* **build:** align Python dependency workflows with uv ([#447](https://github.com/microsoft/physical-ai-toolchain/issues/447)) ([3102e03](https://github.com/microsoft/physical-ai-toolchain/commit/3102e03d6e6c844f1130c278b9c53dddf0119e75))
* **docs:** rename Docusaurus site to Physical AI Toolchain ([#224](https://github.com/microsoft/physical-ai-toolchain/issues/224)) ([cfdf47a](https://github.com/microsoft/physical-ai-toolchain/commit/cfdf47a52be6b8367acb2dd025ca38e4355ee338))
* **infrastructure:** rename boolean variables to `should_` prefix and add missing core variables ([#292](https://github.com/microsoft/physical-ai-toolchain/issues/292)) ([4496593](https://github.com/microsoft/physical-ai-toolchain/commit/4496593bd0b9e03bd6f443e0bc536ba1249ff9a0))
* **python:** move runtime deps to workflow pyproject manifests ([#405](https://github.com/microsoft/physical-ai-toolchain/issues/405)) ([6c5fbeb](https://github.com/microsoft/physical-ai-toolchain/commit/6c5fbeb80ba7d30f1d106d5a731835d355d9515d))


### 📦 Build System

* **build:** add Codecov upload to pytest workflow ([#434](https://github.com/microsoft/physical-ai-toolchain/issues/434)) ([0110c17](https://github.com/microsoft/physical-ai-toolchain/commit/0110c17a161eb2201ac615a74b671894b4b62b87))
* **deps-dev:** bump the npm_and_yarn group across 2 directories with 1 update ([#325](https://github.com/microsoft/physical-ai-toolchain/issues/325)) ([59cf9e6](https://github.com/microsoft/physical-ai-toolchain/commit/59cf9e6265b596b5c64b27a4843f8ce2f7d65cbe))
* **workflows:** enable coverage parameters and fix Pester test infrastructure ([#435](https://github.com/microsoft/physical-ai-toolchain/issues/435)) ([528bbde](https://github.com/microsoft/physical-ai-toolchain/commit/528bbde3cd3530934d0bdb026093e78a05e4eb29))


### 🔧 Miscellaneous

* add gomod to cspell general-technical wordlist ([#362](https://github.com/microsoft/physical-ai-toolchain/issues/362)) ([1f93f47](https://github.com/microsoft/physical-ai-toolchain/commit/1f93f472db61c392b1663217108f15eab75efb21))
* **build:** add codecov.yml for unified coverage reporting ([#430](https://github.com/microsoft/physical-ai-toolchain/issues/430)) ([b0faf70](https://github.com/microsoft/physical-ai-toolchain/commit/b0faf700704b92a2993b443e6f394a8e01641977))
* **build:** add Go toolchain devcontainer feature and Dependabot gomod ([#337](https://github.com/microsoft/physical-ai-toolchain/issues/337)) ([8a36620](https://github.com/microsoft/physical-ai-toolchain/commit/8a36620b9912f696de81dc1e62b3e86d676c1b53))
* **deps:** bump cryptography from 45.0.7 to 46.0.5 in /src/training ([#506](https://github.com/microsoft/physical-ai-toolchain/issues/506)) ([a06434e](https://github.com/microsoft/physical-ai-toolchain/commit/a06434e4493f96d10c64628a0c2ef11f21a66605))
* **deps:** bump minimatch in /src/dataviewer/frontend ([#416](https://github.com/microsoft/physical-ai-toolchain/issues/416)) ([38a7607](https://github.com/microsoft/physical-ai-toolchain/commit/38a76072243cbf12496b5c6ac196f7a78a602c01))
* **deps:** bump pyasn1 from 0.6.2 to 0.6.3 in /training/rl ([#296](https://github.com/microsoft/physical-ai-toolchain/issues/296)) ([7b42cf5](https://github.com/microsoft/physical-ai-toolchain/commit/7b42cf5ca8607b9b25d0ed43472004e9e3438f8c))
* **deps:** bump rollup in /src/dataviewer/frontend ([#417](https://github.com/microsoft/physical-ai-toolchain/issues/417)) ([6302ce4](https://github.com/microsoft/physical-ai-toolchain/commit/6302ce4ccd81800a7a25a75a2422ab75d0478869))
* **deps:** bump the common-dependencies group in /src/common with 3 updates ([#507](https://github.com/microsoft/physical-ai-toolchain/issues/507)) ([db05074](https://github.com/microsoft/physical-ai-toolchain/commit/db050745e523540577a576e3e9d08eaa68929ff5))
* **deps:** bump the github-actions group across 1 directory with 6 updates ([#284](https://github.com/microsoft/physical-ai-toolchain/issues/284)) ([c40eff6](https://github.com/microsoft/physical-ai-toolchain/commit/c40eff653905cc0000980b6e19720370ba925d0f))
* **deps:** bump the github-actions group across 1 directory with 6 updates ([#433](https://github.com/microsoft/physical-ai-toolchain/issues/433)) ([2d9dd4f](https://github.com/microsoft/physical-ai-toolchain/commit/2d9dd4f2985775a78e8d5c1f9bdddc69233308bd))
* **deps:** bump the github-actions group across 1 directory with 6 updates ([#510](https://github.com/microsoft/physical-ai-toolchain/issues/510)) ([c334a64](https://github.com/microsoft/physical-ai-toolchain/commit/c334a64a4f775314a766bc8b0b3334c2a7395ccc))
* **deps:** bump the github-actions group with 2 updates ([#163](https://github.com/microsoft/physical-ai-toolchain/issues/163)) ([f25713e](https://github.com/microsoft/physical-ai-toolchain/commit/f25713eeefec1c7b9a9ebf169ca99b7af36ed167))
* **deps:** bump the inference-dependencies group in /evaluation with 3 updates ([#279](https://github.com/microsoft/physical-ai-toolchain/issues/279)) ([1d2d3dc](https://github.com/microsoft/physical-ai-toolchain/commit/1d2d3dc1871ee142b0e04c7343935c52a47d77b8))
* **deps:** bump the inference-dependencies group in /src/inference with 5 updates ([#508](https://github.com/microsoft/physical-ai-toolchain/issues/508)) ([2852ffb](https://github.com/microsoft/physical-ai-toolchain/commit/2852ffb2edb0e9f8a207656eaf41b6e2d15b47c0))
* **deps:** bump the lerobot-inference-dependencies group in /workflows/azureml with 4 updates ([#511](https://github.com/microsoft/physical-ai-toolchain/issues/511)) ([b7c5773](https://github.com/microsoft/physical-ai-toolchain/commit/b7c5773dd4f570c42b911e07b7da87355a86649a))
* **deps:** bump the npm_and_yarn group across 2 directories with 1 update ([#223](https://github.com/microsoft/physical-ai-toolchain/issues/223)) ([6a261ab](https://github.com/microsoft/physical-ai-toolchain/commit/6a261ab863e67c4141b59f6dde97a85baacafd9d))
* **deps:** bump the training-dependencies group ([#429](https://github.com/microsoft/physical-ai-toolchain/issues/429)) ([66e43f4](https://github.com/microsoft/physical-ai-toolchain/commit/66e43f484634d357af686cdae4b2127ab6ee52ac))
* **deps:** bump tornado from 6.5.4 to 6.5.5 in the uv group across 1 directory ([#172](https://github.com/microsoft/physical-ai-toolchain/issues/172)) ([d6caf29](https://github.com/microsoft/physical-ai-toolchain/commit/d6caf29536e59c617b8ec0487fef7ed97b6a6db1))
* **docs:** correct ms.date tooling and refresh stale documentation ([#349](https://github.com/microsoft/physical-ai-toolchain/issues/349)) ([ccaa1e8](https://github.com/microsoft/physical-ai-toolchain/commit/ccaa1e8bdb43cb1baaaf00276e352049146bbeaa))
* **infrastructure:** add Go module and golangci-lint config for e2e tests ([#347](https://github.com/microsoft/physical-ai-toolchain/issues/347)) ([e0e6bbf](https://github.com/microsoft/physical-ai-toolchain/commit/e0e6bbfe32535b176cc49ba88b8a56892984f938))
* **infrastructure:** add root .terraform-docs.yml configuration ([#312](https://github.com/microsoft/physical-ai-toolchain/issues/312)) ([bb73bbb](https://github.com/microsoft/physical-ai-toolchain/commit/bb73bbb0dacfcf873b0a46a5a3892dbf31347a8d))
* migrate references from Azure-Samples to microsoft/physical-ai-toolchain ([f58f0ef](https://github.com/microsoft/physical-ai-toolchain/commit/f58f0effcc40dfb311d6e899d385264916d6686b))
* **workflows:** update Dependabot, CodeQL, CODEOWNERS, and cspell for dataviewer coverage ([#231](https://github.com/microsoft/physical-ai-toolchain/issues/231)) ([6d8c2e8](https://github.com/microsoft/physical-ai-toolchain/commit/6d8c2e8b78b280ed921c8636cc0d531a237b6d16))


### 🔒 Security

* **deps:** bump mlflow from 3.5.0 to 3.8.0rc0 in /training/rl ([#297](https://github.com/microsoft/physical-ai-toolchain/issues/297)) ([e9929df](https://github.com/microsoft/physical-ai-toolchain/commit/e9929df31b6cfbd6bddfbcf3200cee6813ec1674))
* **deps:** bump the github-actions group across 1 directory with 4 updates ([#344](https://github.com/microsoft/physical-ai-toolchain/issues/344)) ([6826929](https://github.com/microsoft/physical-ai-toolchain/commit/6826929af7091690edfecc31d31f6c1fbd5abe77))
* **deps:** bump the inference-dependencies group in /evaluation with 2 updates ([#339](https://github.com/microsoft/physical-ai-toolchain/issues/339)) ([6804630](https://github.com/microsoft/physical-ai-toolchain/commit/68046304bd11f4b4631405d9f32307c724ad9d1b))
* **deps:** bump the npm_and_yarn group across 3 directories with 1 update ([#361](https://github.com/microsoft/physical-ai-toolchain/issues/361)) ([6760857](https://github.com/microsoft/physical-ai-toolchain/commit/6760857d07a4ba8f88890dc5b11dbf1a187536ca))
* **deps:** bump the training-dependencies group across 1 directory with 54 updates ([#286](https://github.com/microsoft/physical-ai-toolchain/issues/286)) ([d9ae04f](https://github.com/microsoft/physical-ai-toolchain/commit/d9ae04f239a08fc86a267b470d1336105dda4267))
* **deps:** bump the uv group across 3 directories with 1 update ([#360](https://github.com/microsoft/physical-ai-toolchain/issues/360)) ([dfbda06](https://github.com/microsoft/physical-ai-toolchain/commit/dfbda06e60808db8d36c4c8d82d29f7af0f9c203))

## [0.4.0](https://github.com/microsoft/physical-ai-toolchain/compare/v0.3.0...v0.4.0) (2026-02-27)


### ✨ Features

* **deploy:** add PowerShell ports of deployment scripts ([#330](https://github.com/microsoft/physical-ai-toolchain/issues/330)) ([4797563](https://github.com/microsoft/physical-ai-toolchain/commit/47975639965f4dc9a1ae38a2f1f4b034130824fe))
* **deploy:** multi-node GPU support with dynamic OSMO pool configuration ([#410](https://github.com/microsoft/physical-ai-toolchain/issues/410)) ([6c98f05](https://github.com/microsoft/physical-ai-toolchain/commit/6c98f05b1987373454c62457eb14f3001961888b))
* **scripts:** add PowerShell dev environment bootstrap script ([#329](https://github.com/microsoft/physical-ai-toolchain/issues/329)) ([f599104](https://github.com/microsoft/physical-ai-toolchain/commit/f5991048b33010283baf8f5c31857c57b51c2887))
* **scripts:** add SHA staleness checking script and Pester tests ([#321](https://github.com/microsoft/physical-ai-toolchain/issues/321)) ([1d0ccbc](https://github.com/microsoft/physical-ai-toolchain/commit/1d0ccbc3924d1d017005cdc9864fb73fc46f09c2))
* **settings:** replace Black formatter with Ruff in VS Code workspace config ([#323](https://github.com/microsoft/physical-ai-toolchain/issues/323)) ([932a73b](https://github.com/microsoft/physical-ai-toolchain/commit/932a73bc4e4bacff806d4478b48faba088124786))
* **workflows:** configure pytest and ruff toolchain with full remediation and python-lint CI ([#196](https://github.com/microsoft/physical-ai-toolchain/issues/196)) ([06390d1](https://github.com/microsoft/physical-ai-toolchain/commit/06390d180622654b3f71207b2c4796ca56d93883))


### 🐛 Bug Fixes

* **build:** regenerate uv.lock in release-please PR to sync project version ([#346](https://github.com/microsoft/physical-ai-toolchain/issues/346)) ([ef0e704](https://github.com/microsoft/physical-ai-toolchain/commit/ef0e70483213bb75edd7470b15c0a80ffdd0860b)), closes [#322](https://github.com/microsoft/physical-ai-toolchain/issues/322)
* **build:** resolve 255 cspell errors across 51 files ([#345](https://github.com/microsoft/physical-ai-toolchain/issues/345)) ([ab99655](https://github.com/microsoft/physical-ai-toolchain/commit/ab9965503f3abc228580529ee219277ae0ab9ac5))


### 📚 Documentation

* add project governance model and PR inactivity policy ([#343](https://github.com/microsoft/physical-ai-toolchain/issues/343)) ([683a93a](https://github.com/microsoft/physical-ai-toolchain/commit/683a93a4bf67a35babdaa141600bad5e911c5c9e))
* add regression test policy for bug fix PRs ([#320](https://github.com/microsoft/physical-ai-toolchain/issues/320)) ([057653b](https://github.com/microsoft/physical-ai-toolchain/commit/057653b5a8ecd8212cebb15004a37c5808b206ee))
* add threat model and security documentation hub ([#373](https://github.com/microsoft/physical-ai-toolchain/issues/373)) ([bed3045](https://github.com/microsoft/physical-ai-toolchain/commit/bed3045822405bde2872a2a956ed46e4e592b55d))
* create docs/ hub index for documentation navigation ([#368](https://github.com/microsoft/physical-ai-toolchain/issues/368)) ([fb7a217](https://github.com/microsoft/physical-ai-toolchain/commit/fb7a217a383c816d1142f929e654d4b065c7a16d))
* **deploy:** create docs/deploy/ hub and migrate deployment documentation ([#372](https://github.com/microsoft/physical-ai-toolchain/issues/372)) ([57de949](https://github.com/microsoft/physical-ai-toolchain/commit/57de9495ebbf02bc7a764f7c31fca5b5510f6684))
* **docs:** add getting-started hub and quickstart tutorial ([#369](https://github.com/microsoft/physical-ai-toolchain/issues/369)) ([3262f10](https://github.com/microsoft/physical-ai-toolchain/commit/3262f1066b8e81801daff031ee2fb069948f2d5b))
* document internationalization scope as not applicable ([#367](https://github.com/microsoft/physical-ai-toolchain/issues/367)) ([b58fe65](https://github.com/microsoft/physical-ai-toolchain/commit/b58fe654b73d939376ae768ba51cceae9632f92f))


### ♻️ Code Refactoring

* **build:** standardize CI workflows to pwsh with composite action ([#341](https://github.com/microsoft/physical-ai-toolchain/issues/341)) ([c9822f9](https://github.com/microsoft/physical-ai-toolchain/commit/c9822f9587bda885e2b79092e5f8c6af0f0a017f))


### 📦 Build System

* **build:** add CodeQL analysis to PR and main CI orchestrators ([#324](https://github.com/microsoft/physical-ai-toolchain/issues/324)) ([de1d49e](https://github.com/microsoft/physical-ai-toolchain/commit/de1d49e41006c9722183bb9cb414196e3b9a6dbd))
* **build:** add OS matrix and -CI flag to Pester tests workflow ([#195](https://github.com/microsoft/physical-ai-toolchain/issues/195)) ([6806647](https://github.com/microsoft/physical-ai-toolchain/commit/6806647358fc3646b38479be2019bb42cde17305))


### 🔧 Miscellaneous

* **build:** update stale GitHub Actions SHA pins and actionlint version ([#342](https://github.com/microsoft/physical-ai-toolchain/issues/342)) ([86074cd](https://github.com/microsoft/physical-ai-toolchain/commit/86074cd0f8f131fe23af00fb0279160862532c28))
* **deps:** bump azure-core ([#370](https://github.com/microsoft/physical-ai-toolchain/issues/370)) ([e5a30ed](https://github.com/microsoft/physical-ai-toolchain/commit/e5a30ed3582f42bc8642521ca598b25c7ec59360))
* **deps:** bump flask from 3.1.2 to 3.1.3 ([#318](https://github.com/microsoft/physical-ai-toolchain/issues/318)) ([4a1dbe4](https://github.com/microsoft/physical-ai-toolchain/commit/4a1dbe41a19cf5a33f5160b12d1534e55e1cb83b))
* **deps:** bump the python-dependencies group across 1 directory with 4 updates ([#319](https://github.com/microsoft/physical-ai-toolchain/issues/319)) ([e9258ec](https://github.com/microsoft/physical-ai-toolchain/commit/e9258ecb1f5c81e1c77eef7735ee7d3120410335))
* **deps:** bump the training-dependencies group across 1 directory with 11 updates ([#186](https://github.com/microsoft/physical-ai-toolchain/issues/186)) ([67580ac](https://github.com/microsoft/physical-ai-toolchain/commit/67580acd849b1853a1f71b17e4296318184e447d))
* **deps:** bump werkzeug from 3.1.5 to 3.1.6 ([#317](https://github.com/microsoft/physical-ai-toolchain/issues/317)) ([72c64ad](https://github.com/microsoft/physical-ai-toolchain/commit/72c64ad92ed52ec858a556015632bb67f8c14570))

## [0.3.0](https://github.com/microsoft/physical-ai-toolchain/compare/v0.2.0...v0.3.0) (2026-02-19)


### ✨ Features

* add LeRobot imitation learning pipelines for OSMO and Azure ML ([#165](https://github.com/microsoft/physical-ai-toolchain/issues/165)) ([baef32d](https://github.com/microsoft/physical-ai-toolchain/commit/baef32de241def42a2d688a47d1628f182d6f272))
* **linting:** add YAML and GitHub Actions workflow linting via actionlint ([#192](https://github.com/microsoft/physical-ai-toolchain/issues/192)) ([e6c1730](https://github.com/microsoft/physical-ai-toolchain/commit/e6c1730b73c65172a9a6858bcae6536de84f9323))
* **scripts:** add dependency pinning compliance scanning ([#169](https://github.com/microsoft/physical-ai-toolchain/issues/169)) ([5d90d4c](https://github.com/microsoft/physical-ai-toolchain/commit/5d90d4c2608f325dabd8a78b1b67b1917e4024ea))
* **scripts:** add frontmatter validation linting pipeline ([#185](https://github.com/microsoft/physical-ai-toolchain/issues/185)) ([6ff58e3](https://github.com/microsoft/physical-ai-toolchain/commit/6ff58e3a001fc86189fbb79cd5a1f434fbb0114a))
* **scripts:** add verified download utility with hash checking ([#180](https://github.com/microsoft/physical-ai-toolchain/issues/180)) ([063dd69](https://github.com/microsoft/physical-ai-toolchain/commit/063dd692a8ec02c62934040d7a6d983617d38f07))


### 🐛 Bug Fixes

* **build:** remove [double] cast on JaCoCo counter array in coverage threshold check ([#312](https://github.com/microsoft/physical-ai-toolchain/issues/312)) ([6b196de](https://github.com/microsoft/physical-ai-toolchain/commit/6b196de1280a0683f4a14bb19a10662527a237a2))
* **build:** resolve release-please draft race condition ([#311](https://github.com/microsoft/physical-ai-toolchain/issues/311)) ([6af1d8b](https://github.com/microsoft/physical-ai-toolchain/commit/6af1d8b2dc633d62ade95d2722bf469aabe3c60c))
* **scripts:** wrap Get-MarkdownTarget returns in array subexpression ([#314](https://github.com/microsoft/physical-ai-toolchain/issues/314)) ([1c5e757](https://github.com/microsoft/physical-ai-toolchain/commit/1c5e757fbaa78441d95c94dde0aa5459666e8a22))
* **src:** replace checkpoint-specific error message in upload_file ([#178](https://github.com/microsoft/physical-ai-toolchain/issues/178)) ([bc0bc7f](https://github.com/microsoft/physical-ai-toolchain/commit/bc0bc7f396d9386d026de62d49250c3ff3bccb5f))
* **workflows:** add id-token write permission for pester-tests ([#183](https://github.com/microsoft/physical-ai-toolchain/issues/183)) ([5c87ca8](https://github.com/microsoft/physical-ai-toolchain/commit/5c87ca8c9ec8965298d7c21b7ad9951544af2e8d))


### ♻️ Code Refactoring

* **scripts:** align LintingHelpers.psm1 with hve-core upstream ([#193](https://github.com/microsoft/physical-ai-toolchain/issues/193)) ([f24bc04](https://github.com/microsoft/physical-ai-toolchain/commit/f24bc0465aab0ffb255ad122175fc7a1b894742e))
* **scripts:** replace GitHub-only CI wrappers with CIHelpers in linting scripts ([#184](https://github.com/microsoft/physical-ai-toolchain/issues/184)) ([033cc9c](https://github.com/microsoft/physical-ai-toolchain/commit/033cc9cf75c82b2ba9169c3c7f5abea1a098c491))
* **src:** standardize os.environ usage in inference upload script ([#194](https://github.com/microsoft/physical-ai-toolchain/issues/194)) ([5a82581](https://github.com/microsoft/physical-ai-toolchain/commit/5a82581f89fb7e2c0b88f168a7735707788f087c))


### 🔧 Miscellaneous

* **scripts:** add Pester test runner and fix test configuration ([#176](https://github.com/microsoft/physical-ai-toolchain/issues/176)) ([4e54ae2](https://github.com/microsoft/physical-ai-toolchain/commit/4e54ae2330b09a437f5bbfb0a9832f971852058f))

## [0.2.0](https://github.com/microsoft/physical-ai-toolchain/compare/v0.1.0...v0.2.0) (2026-02-12)


### ✨ Features

* **build:** add automatic milestone closure on release publish ([#148](https://github.com/microsoft/physical-ai-toolchain/issues/148)) ([18c72e5](https://github.com/microsoft/physical-ai-toolchain/commit/18c72e56f53afef39eb0db16ad6246f6ddc43827))


### 🐛 Bug Fixes

* **build:** restore release-please skip guard on release PR merge ([#147](https://github.com/microsoft/physical-ai-toolchain/issues/147)) ([d8ade84](https://github.com/microsoft/physical-ai-toolchain/commit/d8ade846074d9b184959715775184b2dc3284af4))
* **workflows:** quote if expression to resolve YAML syntax error ([#172](https://github.com/microsoft/physical-ai-toolchain/issues/172)) ([b3120a6](https://github.com/microsoft/physical-ai-toolchain/commit/b3120a6b07253fb494da20d1e2acdf9f1bc6a627))


### 📚 Documentation

* add deployer-facing security considerations ([#161](https://github.com/microsoft/physical-ai-toolchain/issues/161)) ([1f5c110](https://github.com/microsoft/physical-ai-toolchain/commit/1f5c1101efe80d3564e8eb5204cd52f75dba116c))
* add hve-core onboarding to README and contributing guides ([#153](https://github.com/microsoft/physical-ai-toolchain/issues/153)) ([8fb63bb](https://github.com/microsoft/physical-ai-toolchain/commit/8fb63bbc0c2543a1cf24a15fbbe7020dd4c16c47))
* add testing requirements to CONTRIBUTING.md ([#150](https://github.com/microsoft/physical-ai-toolchain/issues/150)) ([0116c4f](https://github.com/microsoft/physical-ai-toolchain/commit/0116c4f9e6c45e29327bb6e0f59af140237462fa))
* **contributing:** add accessibility best practices statement ([#166](https://github.com/microsoft/physical-ai-toolchain/issues/166)) ([2d5f239](https://github.com/microsoft/physical-ai-toolchain/commit/2d5f2399bcb39bff8c5ae276cfe77524297c4e48))
* **contributing:** publish 12-month roadmap ([#159](https://github.com/microsoft/physical-ai-toolchain/issues/159)) ([f158463](https://github.com/microsoft/physical-ai-toolchain/commit/f158463fcca6d2eeaab48c88da3a242ed6b2df7d))
* create comprehensive CONTRIBUTING.md ([#119](https://github.com/microsoft/physical-ai-toolchain/issues/119)) ([9c60073](https://github.com/microsoft/physical-ai-toolchain/commit/9c600734b139099e7f6f0976a2791de13a19096c))
* define documentation maintenance policy ([#162](https://github.com/microsoft/physical-ai-toolchain/issues/162)) ([bd750ed](https://github.com/microsoft/physical-ai-toolchain/commit/bd750ed2a7943680b5ee0ab24e9e77899d2b9c0c))
* **deploy:** standardize installation and uninstallation terminology in README files ([#168](https://github.com/microsoft/physical-ai-toolchain/issues/168)) ([43427f3](https://github.com/microsoft/physical-ai-toolchain/commit/43427f323aaaa30888742875949497106543a9b7))
* **docs:** add test execution and cleanup instructions ([#167](https://github.com/microsoft/physical-ai-toolchain/issues/167)) ([d83b20e](https://github.com/microsoft/physical-ai-toolchain/commit/d83b20e1714da98d67ea11145def056a710ff7e2))
* **docs:** decompose and relocate detailed contributing guide ([#156](https://github.com/microsoft/physical-ai-toolchain/issues/156)) ([3783400](https://github.com/microsoft/physical-ai-toolchain/commit/3783400811df619cc4b9b150048ccea032fa9351))
* **scripts:** document submit script CLI arguments ([#123](https://github.com/microsoft/physical-ai-toolchain/issues/123)) ([adabdd5](https://github.com/microsoft/physical-ai-toolchain/commit/adabdd51e8db0e734d0875a070bc4ded338ec8a6))
* **src:** add docstrings to training utils context module ([#157](https://github.com/microsoft/physical-ai-toolchain/issues/157)) ([b6312f5](https://github.com/microsoft/physical-ai-toolchain/commit/b6312f5942b32bf4f0f94625baec100279c674b9))
* **src:** add Google-style docstrings to metrics module ([#151](https://github.com/microsoft/physical-ai-toolchain/issues/151)) ([311886c](https://github.com/microsoft/physical-ai-toolchain/commit/311886c5740ba4d5ab98a215998514772c9bb965))
* **src:** expand Google-style docstrings for training utils env module ([#131](https://github.com/microsoft/physical-ai-toolchain/issues/131)) ([29ab4f8](https://github.com/microsoft/physical-ai-toolchain/commit/29ab4f802fd023a3b1ec6318b449ab60356b28fa))


### 🔧 Miscellaneous

* **deps:** bump protobuf from 6.33.3 to 6.33.5 ([#51](https://github.com/microsoft/physical-ai-toolchain/issues/51)) ([cab59e6](https://github.com/microsoft/physical-ai-toolchain/commit/cab59e620678d3056180ffc152bfd0789891f4ac))
* **deps:** bump the github-actions group with 4 updates ([#155](https://github.com/microsoft/physical-ai-toolchain/issues/155)) ([f73898f](https://github.com/microsoft/physical-ai-toolchain/commit/f73898f9b6f9b919a819633cdc7b200f41eb145b))
* **deps:** bump the python-dependencies group across 1 directory with 11 updates ([#134](https://github.com/microsoft/physical-ai-toolchain/issues/134)) ([09331ea](https://github.com/microsoft/physical-ai-toolchain/commit/09331ea3757681f1fca2acf9eca61043718cb409))

## [0.1.0](https://github.com/microsoft/physical-ai-toolchain/compare/v0.0.1...v0.1.0) (2026-02-07)


### ✨ Features

* **.github:** Add GitHub workflows from hve-core ([#22](https://github.com/microsoft/physical-ai-toolchain/issues/22)) ([96ae111](https://github.com/microsoft/physical-ai-toolchain/commit/96ae111622bc751f38d616803c85f5ab6e5dcca4))
* add PR template and YAML issue form templates ([#16](https://github.com/microsoft/physical-ai-toolchain/issues/16)) ([059ac48](https://github.com/microsoft/physical-ai-toolchain/commit/059ac48d133eb7fb6013408e2df74de948769293))
* **automation:** add runbook automation ([#25](https://github.com/microsoft/physical-ai-toolchain/issues/25)) ([c8f0fd4](https://github.com/microsoft/physical-ai-toolchain/commit/c8f0fd4f8bc661f3caff1d737e4c05ad2bb70d19))
* **build:** integrate release-please bot with GitHub App auth and CI gating ([#139](https://github.com/microsoft/physical-ai-toolchain/issues/139)) ([f930b6b](https://github.com/microsoft/physical-ai-toolchain/commit/f930b6bcb569b624622c73a3c4893a50fa26dbaa))
* **build:** migrate package management to uv ([#43](https://github.com/microsoft/physical-ai-toolchain/issues/43)) ([cfe028f](https://github.com/microsoft/physical-ai-toolchain/commit/cfe028f3943192793af932bbadf83e50d50c375e))
* **cleanup:** remove NGC token requirement and add infrastructure cleanup documentation ([#31](https://github.com/microsoft/physical-ai-toolchain/issues/31)) ([51ed7d6](https://github.com/microsoft/physical-ai-toolchain/commit/51ed7d683e39d12cdc82b53ba83b8a71e75c25e6))
* **deploy:** add Azure PowerShell modules for automation runbooks ([#44](https://github.com/microsoft/physical-ai-toolchain/issues/44)) ([0148921](https://github.com/microsoft/physical-ai-toolchain/commit/01489211b29b762453669a04ef07433465114496))
* **deploy:** add policy export and inference scripts for ONNX/JIT ([#21](https://github.com/microsoft/physical-ai-toolchain/issues/21)) ([94b6ff1](https://github.com/microsoft/physical-ai-toolchain/commit/94b6ff1aa69f4643292ca75707bd8e7cd74c55bf))
* **deploy:** add support for workload identity osmo datasets ([#24](https://github.com/microsoft/physical-ai-toolchain/issues/24)) ([c948a3c](https://github.com/microsoft/physical-ai-toolchain/commit/c948a3c8bf47dfbb5d78d6b70ae71651de020743))
* **deploy:** implement robotics infrastructure with Azure resources ([#9](https://github.com/microsoft/physical-ai-toolchain/issues/9)) ([103e31e](https://github.com/microsoft/physical-ai-toolchain/commit/103e31eb481356b3c19d0ed9f7e8a4b320dd6d1b))
* **deploy:** integrate Azure Key Vault secrets sync via CSI driver ([#32](https://github.com/microsoft/physical-ai-toolchain/issues/32)) ([864006b](https://github.com/microsoft/physical-ai-toolchain/commit/864006b3af8dabd17d73748dfbc610c10fc3e1a1))
* **devcontainer:** enhance development environment setup ([#28](https://github.com/microsoft/physical-ai-toolchain/issues/28)) ([a930ac0](https://github.com/microsoft/physical-ai-toolchain/commit/a930ac00565fcb29ef01c3df3e58d40b0aa196ee))
* **docs:** documentation updates ([#27](https://github.com/microsoft/physical-ai-toolchain/issues/27)) ([3fcc6b6](https://github.com/microsoft/physical-ai-toolchain/commit/3fcc6b6f69439f112e47b42e292abfd747ff282c))
* initial osmo workflow and training on Azure ([#1](https://github.com/microsoft/physical-ai-toolchain/issues/1)) ([ff5f7df](https://github.com/microsoft/physical-ai-toolchain/commit/ff5f7df55ddb474e72e8f508120b1c69a24d9d7d))
* **instructions:** add Copilot instruction files and clean up VS Code settings ([#36](https://github.com/microsoft/physical-ai-toolchain/issues/36)) ([6d8fb2c](https://github.com/microsoft/physical-ai-toolchain/commit/6d8fb2c14f7703cd3ee233a11d4370c5d35ecb75))
* **repo:** add root capabilities and reorganize README ([#17](https://github.com/microsoft/physical-ai-toolchain/issues/17)) ([4aede6f](https://github.com/microsoft/physical-ai-toolchain/commit/4aede6fb33fecd066748c198d12fee288b427596))
* **robotics:** refactor infra and finish OSMO and AzureML support ([#23](https://github.com/microsoft/physical-ai-toolchain/issues/23)) ([3b15665](https://github.com/microsoft/physical-ai-toolchain/commit/3b15665dc563253a2460c01f8057d8719e97a815))
* **scripts:** add CIHelpers.psm1 shared CI module ([#129](https://github.com/microsoft/physical-ai-toolchain/issues/129)) ([467e071](https://github.com/microsoft/physical-ai-toolchain/commit/467e071381e559d143b271a3f898c88ca2f67d03))
* **scripts:** add RSL-RL 3.x TensorDict compatibility and training backend selection ([#26](https://github.com/microsoft/physical-ai-toolchain/issues/26)) ([4986caa](https://github.com/microsoft/physical-ai-toolchain/commit/4986caa92d2dbcae874b6f95f9fe3d952471c565))
* **scripts:** reduce payload size by excluding any cache from python ([#29](https://github.com/microsoft/physical-ai-toolchain/issues/29)) ([8a20b46](https://github.com/microsoft/physical-ai-toolchain/commit/8a20b46c869cfee5e2f587f63b78d3a1f9164b25))
* **training:** add MLflow machine metrics collection ([#5](https://github.com/microsoft/physical-ai-toolchain/issues/5)) ([1f79dc0](https://github.com/microsoft/physical-ai-toolchain/commit/1f79dc0439072af7b3a6407e7b460d166147217d))


### 🐛 Bug Fixes

* **build:** strip CHANGELOG frontmatter and fix initial version for release-please ([#142](https://github.com/microsoft/physical-ai-toolchain/issues/142)) ([81755ec](https://github.com/microsoft/physical-ai-toolchain/commit/81755ecd86100f0507d768c980ccae4ebe76a9df))
* **deploy:** ignore changes to zone in PostgreSQL flexible server lifecycle ([#34](https://github.com/microsoft/physical-ai-toolchain/issues/34)) ([80ef4a6](https://github.com/microsoft/physical-ai-toolchain/commit/80ef4a625bb50090477b5bd23a797aa414c2c1a3))
* **deploy:** resolve hybrid cluster deployment issues ([#39](https://github.com/microsoft/physical-ai-toolchain/issues/39)) ([69f69d7](https://github.com/microsoft/physical-ai-toolchain/commit/69f69d7dfb96fde6cf831983971e3ae9af67232f))
* **ps:** avoid PowerShell ternary for compatibility ([#124](https://github.com/microsoft/physical-ai-toolchain/issues/124)) ([b8da8a1](https://github.com/microsoft/physical-ai-toolchain/commit/b8da8a1a353b4edec28ff9958a3b3810be542912))
* **script:** replace osmo-dev function with direct osmo command usage ([#30](https://github.com/microsoft/physical-ai-toolchain/issues/30)) ([29c8b6d](https://github.com/microsoft/physical-ai-toolchain/commit/29c8b6d9b3c11ca2e145454a2aaea3dd8f782ad2))


### 📚 Documentation

* **deploy:** enhance VPN and network configuration documentation ([#38](https://github.com/microsoft/physical-ai-toolchain/issues/38)) ([2992f07](https://github.com/microsoft/physical-ai-toolchain/commit/2992f0743265387a3c754b650d8641e41f9ab9c0))
* **deploy:** enhance VPN documentation with detailed client setup instructions ([#35](https://github.com/microsoft/physical-ai-toolchain/issues/35)) ([4ded515](https://github.com/microsoft/physical-ai-toolchain/commit/4ded515697bd8b3c0235c5487d01b9a8e35950e5))
* enhance README with architecture diagram and deployment documentation ([#33](https://github.com/microsoft/physical-ai-toolchain/issues/33)) ([7baf903](https://github.com/microsoft/physical-ai-toolchain/commit/7baf90331684647c39d18bfe70e8f5fc28499eec))
* update README.md with architecture overview and repository structure ([4accbdb](https://github.com/microsoft/physical-ai-toolchain/commit/4accbdbd6ff088e7e898f1222d99b030b78daffa))


### 🔧 Miscellaneous

* **deps:** bump azure-core from 1.28.0 to 1.38.0 ([#45](https://github.com/microsoft/physical-ai-toolchain/issues/45)) ([d25d14e](https://github.com/microsoft/physical-ai-toolchain/commit/d25d14e151af8b0b79fc50ff514ec61531715cb5))
* **deps:** bump azure-core from 1.28.0 to 1.38.0 in /src/training ([#42](https://github.com/microsoft/physical-ai-toolchain/issues/42)) ([b1bd20c](https://github.com/microsoft/physical-ai-toolchain/commit/b1bd20c478e1b69312627104f81819fd9ac305de))
* **deps:** bump pyasn1 from 0.6.1 to 0.6.2 ([#46](https://github.com/microsoft/physical-ai-toolchain/issues/46)) ([97a3b2c](https://github.com/microsoft/physical-ai-toolchain/commit/97a3b2c1cda50023255aeaf20a0df1c33f85744a))
* **instructions:** add general instructions copilot instructions ([44fc94d](https://github.com/microsoft/physical-ai-toolchain/commit/44fc94d70caf9d07dc2c402bf71c6e761ec9566d))
* **settings:** add development environment configuration ([c3c8e32](https://github.com/microsoft/physical-ai-toolchain/commit/c3c8e32c46429e0c131638c77f1daf70322976d3))
* **settings:** migrate cspell to modular dictionary structure ([#15](https://github.com/microsoft/physical-ai-toolchain/issues/15)) ([ff8ffd2](https://github.com/microsoft/physical-ai-toolchain/commit/ff8ffd243fa349a9f4b7023157e2a74ea5bab217))
* **training:** refactor SKRL training scripts for maintainability ([#4](https://github.com/microsoft/physical-ai-toolchain/issues/4)) ([8cdadac](https://github.com/microsoft/physical-ai-toolchain/commit/8cdadacd367ae4620d4fe10978936d1c6840476c))
