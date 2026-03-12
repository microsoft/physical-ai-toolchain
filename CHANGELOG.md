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
