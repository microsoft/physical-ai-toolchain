---
title: SO-101 inference benchmark results
description: Synthetic ACT and Pi0.5 inference benchmarks plus real-hardware ACT rollout measurements
ms.date: 2026-09-21
---

Synthetic benchmarks compare direct, plaintext-offload, and AES-GCM-offload
inference for ACT and Pi0.5. Real-hardware measurements compare the original
client-prepared `float32` observation path with server-prepared `uint8`
observations.

## 🧪 Synthetic benchmark setup

The synthetic benchmark ran on September 21, 2026, on the ARM64 Kubernetes GPU
node. Each mode used the same deterministic 1,843,224-byte observation payload,
10 warm-up calls, and 100 measured calls.

| Parameter           | Value                                      |
|---------------------|--------------------------------------------|
| Policies            | ACT and Pi0.5                              |
| Inference modes     | Direct, plaintext offload, AES-GCM offload |
| Observation payload | 1,843,224 bytes                            |
| Warm-up calls       | 10                                         |
| Measured calls      | 100                                        |
| ACT action chunk    | 100 actions                                |
| Pi0.5 action chunk  | 10 actions                                 |

These measurements are single benchmark runs. Use them as reference values for
comparing modes on the tested node, not as cross-platform performance targets.

## 📈 Synthetic benchmark results

Overall latency includes action-chunk refills and actions returned from the
existing queue.

| Policy | Mode              | Mean ms | p50 ms |  p95 ms |  p99 ms |  Max ms | Throughput Hz |
|--------|-------------------|--------:|-------:|--------:|--------:|--------:|--------------:|
| ACT    | Direct            |   3.336 |  2.723 |   5.910 |  10.008 |  23.068 |       299.776 |
| ACT    | Plaintext offload |  11.103 | 10.099 |  17.468 |  18.670 |  33.165 |        90.067 |
| ACT    | AES-GCM offload   |  13.047 | 12.727 |  15.749 |  17.191 |  34.232 |        76.646 |
| Pi0.5  | Direct            |  45.481 |  5.004 | 408.625 | 421.180 | 421.199 |        21.987 |
| Pi0.5  | Plaintext offload |  53.258 | 11.603 | 426.043 | 440.674 | 441.082 |        18.776 |
| Pi0.5  | AES-GCM offload   |  59.186 | 16.320 | 441.252 | 456.492 | 462.538 |        16.896 |

Action-chunk timing separates model forward passes from queued action
retrieval.

| Policy | Mode              | Refill calls | Refill mean ms | Queued calls | Queued mean ms |
|--------|-------------------|-------------:|---------------:|-------------:|---------------:|
| ACT    | Direct            |            1 |         23.068 |           99 |          3.137 |
| ACT    | Plaintext offload |            1 |         33.165 |           99 |         10.880 |
| ACT    | AES-GCM offload   |            1 |         34.232 |           99 |         12.833 |
| Pi0.5  | Direct            |           10 |        409.786 |           90 |          5.003 |
| Pi0.5  | Plaintext offload |           10 |        428.327 |           90 |         11.584 |
| Pi0.5  | AES-GCM offload   |           10 |        445.819 |           90 |         16.227 |

For the 1.84 MB request, AES-GCM adds 1.944 ms to ACT mean latency and
5.928 ms to Pi0.5 mean latency relative to plaintext offload. Pi0.5 percentile
values reflect its ten model-forward chunk refills; the remaining 90 calls
return queued actions.

## 🧪 Real-hardware measurement setup

| Parameter           | Value                                                 |
|---------------------|-------------------------------------------------------|
| Robot               | SO-101 follower                                       |
| Policy              | ACT                                                   |
| Cameras             | Two OpenCV cameras, 640x480 RGB at 30 FPS             |
| Rollout duration    | 60 seconds                                            |
| Target control rate | 30 Hz                                                 |
| Inference           | Synchronous GPU offload                               |
| Transport runtime   | 256 KiB TCP receive chunks with buffered accumulation |
| Timing interval     | Cumulative report every 100 control iterations        |

Both measurements used the same robot, policy, cameras, control target, and
offload runtime. Each row represents one 60-second rollout, so these results
measure the observed integration rather than a multi-run statistical
benchmark.

The payload sizes below include the two camera images only. Joint state and
returned actions are small relative to the image tensors.

## 📊 Summary

| Metric                    | Client `float32` | Server-prepared `uint8` | Change |
|---------------------------|-----------------:|------------------------:|-------:|
| Completed iterations      |            1,577 |                   1,760 | +11.6% |
| Elapsed time              |         60.027 s |                60.008 s | -0.03% |
| Achieved control rate     |         26.27 Hz |                29.33 Hz | +11.6% |
| Image request payload     |          7.37 MB |                 1.84 MB | -75.0% |
| Client inference/RPC mean |        34.618 ms |               13.035 ms | -62.3% |
| Client inference/RPC p50  |        31.854 ms |               11.609 ms | -63.6% |
| Client inference/RPC p95  |        44.124 ms |               18.081 ms | -59.0% |
| Action dispatch mean      |        35.209 ms |               13.800 ms | -60.8% |

The `uint8` path reaches 97.8% of the configured 30 Hz rate. Its measured
control work finishes before the 33.33 ms control period on typical
iterations, so target-rate pacing rather than inference throughput becomes
the steady-state limit.

## 🔢 Client timing

### Client-prepared float32 observations

The client converts both images from HWC `uint8` to contiguous CHW `float32`,
normalizes them, and sends approximately 7.37 MB to the policy RPC on every
control iteration.

| Stage                   | Calls | Mean ms | p50 ms | p95 ms |  Max ms |
|-------------------------|------:|--------:|-------:|-------:|--------:|
| Action dispatch         | 1,577 |  35.209 | 32.448 | 44.964 | 772.971 |
| Dataset frame           | 1,577 |   0.038 |  0.038 |  0.044 |   0.117 |
| Camera read             | 3,154 |   0.010 |  0.010 |  0.017 |   0.073 |
| Inference total         | 1,577 |  34.618 | 31.854 | 44.124 | 772.310 |
| Observation processor   | 1,577 |   0.015 |  0.014 |  0.020 |   0.122 |
| Policy RPC              | 1,577 |  25.346 | 22.645 | 33.662 | 581.951 |
| Observation preparation | 1,577 |   5.401 |  5.211 |  7.392 | 152.489 |
| Robot observation       | 1,577 |   1.388 |  1.341 |  1.677 |   4.500 |
| Robot action write      | 1,577 |   0.434 |  0.339 |  0.977 |   5.515 |
| Serial read             | 1,577 |   1.228 |  1.176 |  1.509 |   4.324 |
| Serial write            | 1,577 |   0.384 |  0.289 |  0.929 |   5.468 |

### Server-prepared uint8 observations

The client wraps the original HWC arrays as CPU tensors without image
normalization or layout conversion. The GPU server converts and processes the
1.84 MB `uint8` request before policy execution.

| Stage                 | Calls | Mean ms | p50 ms | p95 ms |  Max ms |
|-----------------------|------:|--------:|-------:|-------:|--------:|
| Action dispatch       | 1,760 |  13.800 | 12.361 | 18.798 | 587.316 |
| Dataset frame         | 1,760 |   0.083 |  0.081 |  0.107 |   0.189 |
| Camera read           | 3,520 |   0.010 |  0.010 |  0.017 |   0.072 |
| Inference/RPC total   | 1,760 |  13.035 | 11.609 | 18.081 | 586.103 |
| Observation processor | 1,760 |   0.032 |  0.031 |  0.041 |   0.123 |
| Robot observation     | 1,760 |   1.503 |  1.431 |  1.813 |  10.536 |
| Robot action write    | 1,760 |   0.429 |  0.353 |  0.720 |   4.226 |
| Serial read           | 1,760 |   1.287 |  1.218 |  1.552 |  10.293 |
| Serial write          | 1,760 |   0.372 |  0.294 |  0.662 |   4.161 |

## 🖥️ Server timing for uint8 observations

Server timing isolates compute after the request is decoded. The difference
between the 13.035 ms client inference/RPC mean and the 5.773 ms server total
is approximately 7.26 ms for client serialization, transport, server decode,
response transport, and client decode.

| Stage                  | Calls | Mean ms | p50 ms | p95 ms |  Max ms |
|------------------------|------:|--------:|-------:|-------:|--------:|
| Complete server action | 1,760 |   5.773 |  4.808 |  6.606 | 567.148 |
| Image preparation      | 1,760 |   2.772 |  2.644 |  3.999 |  44.666 |
| Policy preprocessor    | 1,760 |   0.427 |  0.405 |  0.483 |  23.736 |
| Policy `select_action` | 1,760 |   1.742 |  1.275 |  1.415 | 492.402 |
| Policy postprocessor   | 1,760 |   0.598 |  0.204 |  0.730 |  47.322 |

ACT returns queued actions on most iterations and periodically replenishes its
action chunk with a model forward pass. The low policy median and high maximum
reflect these two execution modes. The periodic refill spikes can exceed one
control period, but the rollout sustains 29.33 Hz over the full measurement.

## 📝 Interpretation

The original path spends 5.401 ms preparing images on the client and 25.346 ms
inside the complete policy RPC. The raw path moves image preparation and both
policy processor pipelines to the server while reducing image transport by
5.53 MB per iteration.

Serial communication and camera access remain below 2 ms per iteration and do
not limit the control rate. After the change, the median end-to-end
inference/RPC latency is 11.609 ms, leaving sufficient time for robot I/O and
30 Hz pacing.
