# PAIDF

Reserved for AVOPS integration with the NVIDIA Physical AI Data Factory (PAIDF) augmentation pipeline.

PAIDF augments camera data (images, video, and text) with generative models such as Cosmos Transfer and Cosmos Predict, and can verify the results. The upstream source is [NVIDIA/paidf-augmentation](https://github.com/NVIDIA/paidf-augmentation) under Apache-2.0.

## Status

This folder has no code yet. Nothing in `AVOPS/scripts`, `AVOPS/ODD`, or `AVOPS/carla-cosmos` uses it: the CARLA + Cosmos Transfer pipeline runs its own task runner and does not depend on PAIDF.

## Guidelines

* Add AVOPS-specific PAIDF configuration, scripts, and documentation here.
* Do not commit the upstream PAIDF source. Clone it at run time and keep upstream licenses and notices with that clone.
* Keep local paths, endpoints, and credentials out of tracked files.
