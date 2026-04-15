---
sidebar_position: 12
title: Fuzzing and Property-Based Testing
description: Running fuzz targets and property-based tests for Python and TypeScript code
author: Microsoft Robotics-AI Team
ms.date: 2026-04-15
ms.topic: how-to
keywords:
  - fuzzing
  - property-based testing
  - atheris
  - hypothesis
  - fast-check
  - security
  - testing
---

This repository uses fuzz testing and property-based testing to find edge cases in input validation, data transformation, and serialization code. Python targets run under Atheris (coverage-guided fuzzing) and Hypothesis (property-based testing). TypeScript targets use fast-check for property-based testing.

## Architecture

| Layer                     | Framework                                        | Scope                                             |
|---------------------------|--------------------------------------------------|---------------------------------------------------|
| Coverage-guided fuzzing   | [Atheris](https://github.com/google/atheris)     | Python functions handling untrusted input         |
| Python property tests     | [Hypothesis](https://hypothesis.readthedocs.io/) | Deterministic pytest classes in the fuzz harness  |
| TypeScript property tests | [fast-check](https://fast-check.dev/)            | Pure utility functions in the dataviewer frontend |

Python fuzz regression tests run in a dedicated CI workflow that uploads coverage under the `pytest-fuzz` Codecov flag. TypeScript property tests run through the existing vitest workflow and merge into the `vitest` flag.

## Python Fuzz Harness

The fuzz harness at `tests/fuzz_harness.py` operates in dual mode:

| Mode    | Trigger                        | Behavior                                                           |
|---------|--------------------------------|--------------------------------------------------------------------|
| Pytest  | `uv run pytest`                | Deterministic test classes exercise targets with controlled inputs |
| Atheris | `python tests/fuzz_harness.py` | Coverage-guided fuzzing with randomized byte streams               |

### Running Pytest Mode

```bash
uv sync --group dev
uv run pytest tests/fuzz_harness.py -v
```

All fuzz targets produce deterministic test classes prefixed with `Test*`. These run as part of the fuzz regression workflow and contribute to the `pytest-fuzz` Codecov flag.

### Running Atheris Mode

Atheris requires a separate install because it depends on native libFuzzer bindings:

```bash
uv sync --group dev --group fuzz
uv run python tests/fuzz_harness.py
```

Atheris mode dispatches randomized bytes to all registered fuzz targets. Crash artifacts are written to `logs/fuzz-crashes/`. The harness creates this directory automatically.

### Seed Corpus

The harness auto-includes `tests/fuzz-corpus/` when the directory exists. Seed files give the fuzzer meaningful starting points so it reaches deep code paths faster than random byte generation alone.

Each seed file is a raw binary blob whose first byte selects the target via `data[0] % 9`, and remaining bytes feed `FuzzedDataProvider`.

#### Generating Seeds

```bash
python3 tests/generate_fuzz_corpus.py
```

This creates 48 seed files covering all 9 targets with valid inputs, boundary values, and attack patterns (path traversal, null bytes, CRLF injection, NaN/Inf floats).

#### Seed Organization

| Prefix | Target                            |
|--------|-----------------------------------|
| `t0_`  | `fuzz_validate_blob_path`         |
| `t1_`  | `fuzz_get_validation_error`       |
| `t2_`  | `fuzz_extract_from_value`         |
| `t3_`  | `fuzz_extract_from_tracking_data` |
| `t4_`  | `fuzz_sanitize_user_string`       |
| `t5_`  | `fuzz_sanitize_nested_value`      |
| `t6_`  | `fuzz_validate_safe_string`       |
| `t7_`  | `fuzz_dataset_id_to_blob_prefix`  |
| `t8_`  | `fuzz_datetime_encoder`           |

When adding a new fuzz target, add corresponding seeds in `generate_fuzz_corpus.py` and re-run the generator.

### Current Targets

| Target               | Module                                                          | Function                                                                 |
|----------------------|-----------------------------------------------------------------|--------------------------------------------------------------------------|
| Blob path validation | `data-management/tools/blob_path_validator.py`                  | `validate_blob_path`, `get_validation_error`                             |
| Metrics extraction   | `training/utils/metrics.py`                                     | `_extract_from_value`, `_extract_from_tracking_data`                     |
| Input sanitization   | `data-management/viewer/backend/src/api/validation.py`          | `sanitize_user_string`, `_sanitize_nested_value`, `validate_safe_string` |
| Storage paths        | `data-management/viewer/backend/src/api/storage/paths.py`       | `dataset_id_to_blob_prefix`                                              |
| JSON serialization   | `data-management/viewer/backend/src/api/storage/serializers.py` | `DateTimeEncoder`                                                        |

### Adding a Fuzz Target

1. Add a fuzz function following the `fuzz_*` naming convention:

```python
def fuzz_my_function(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    value = fdp.ConsumeUnicodeNoSurrogates(256)
    with suppress(ValueError):
        my_function(value)
```

1. Register it in the `_FUZZ_TARGETS` list at the bottom of the harness.

1. Add a corresponding `Test*` class with deterministic edge-case inputs:

```python
class TestMyFunction:
    def test_empty_input(self) -> None:
        assert my_function("") == expected

    def test_boundary_case(self) -> None:
        assert my_function(boundary_value) == expected
```

1. Run the tests to confirm both modes work:

```bash
uv run pytest tests/fuzz_harness.py -v
```

## TypeScript Property Tests

Property-based tests for the dataviewer frontend use fast-check with Vitest. Test files follow the `*.property.test.ts` naming convention.

### Running Property Tests

```bash
cd data-management/viewer/frontend
npx vitest run --reporter=verbose
```

Property tests run as part of the standard vitest suite and contribute to the `vitest` Codecov flag.

### Current Test Files

| File                                                           | Module Under Test                                                   |
|----------------------------------------------------------------|---------------------------------------------------------------------|
| `src/lib/__tests__/api-client.property.test.ts`                | `snakeToCamel`, `transformKeys`                                     |
| `src/lib/__tests__/api-client-fuzz.test.ts`                    | `snakeToCamel`, `transformKeys` (adversarial Unicode, deep nesting) |
| `src/lib/__tests__/playback-utils.property.test.ts`            | Playback range resolution, frame clamping, FPS computation          |
| `src/lib/__tests__/edit-store-frame-utils.property.test.ts`    | Frame index conversion with insertions and removals                 |
| `src/lib/__tests__/trajectory-graph-geometry.property.test.ts` | Coordinate math for trajectory visualization                        |

### Writing a Property Test

Target pure functions with well-defined input/output contracts. Use arbitraries that match the function's domain:

```typescript
import fc from 'fast-check'

describe('myFunction', () => {
  it('satisfies some invariant', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 1000 }), (input) => {
        const result = myFunction(input)
        expect(result).toBeGreaterThanOrEqual(0)
      }),
    )
  })
})
```

Prefer these property patterns:

| Pattern      | Description                                       |
|--------------|---------------------------------------------------|
| Invariant    | Output always satisfies a constraint              |
| Idempotence  | Applying the function twice gives the same result |
| Roundtrip    | Encode then decode returns the original value     |
| Monotonicity | Larger input produces larger or equal output      |
| Bounds       | Output stays within a known range                 |

## Hypothesis Configuration

Global Hypothesis settings live in `pyproject.toml`:

```toml
[tool.hypothesis]
max_examples = 50
deadline = 500
```

These settings apply to all Hypothesis-based tests. `max_examples` controls the number of random inputs per test case. `deadline` sets the per-example timeout in milliseconds.

## Coverage Integration

Fuzz and property test coverage merges into existing Codecov flags:

| Test type                 | Codecov flag        | Coverage file                     |
|---------------------------|---------------------|-----------------------------------|
| Python fuzz harness       | `pytest-fuzz`       | `logs/coverage-fuzz.xml`          |
| Dataviewer backend        | `pytest-dataviewer` | `logs/coverage-dataviewer.xml`    |
| TypeScript property tests | `vitest`            | `coverage/cobertura-coverage.xml` |

Per-flag patch coverage status is set to `informational: true` so fuzz coverage differences never block PRs. This follows the pattern used in [microsoft/hve-core](https://github.com/microsoft/hve-core).

## Related Documentation

- [Security Review](security-review.md) for security testing requirements
- [Prerequisites](prerequisites.md) for required tool versions
- [Deployment Validation](deployment-validation.md) for validation levels
