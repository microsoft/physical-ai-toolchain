# Data Analysis Methods

<!-- cspell:ignore luma regrasp regrasps -->

Each method names the function that implements it, its parameters, and the check that keeps its result honest. Thresholds are module constants in `scripts/observe.py` and `scripts/media.py`; every report lists the values it used in its method section.

## Layout Resolution

`resolve_layout()` reads `meta/info.json` and decides what every later metric means. Confirm its choices before trusting any number.

| Element       | Rule                                                                                                                                                  |
|---------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| State         | `observation.state`, else the first numeric `observation.*` vector; override with `--state`                                                           |
| Action        | `action` when present; override with `--action`                                                                                                       |
| Pairing       | State and action pair when widths match and names agree after stripping a `.`, `_`, or `-` suffix of `target`, `cmd`, `command`, `goal`, or `desired` |
| Groups        | State names split by side (left, right, or none) and into gripper (`grip`, `finger`, `claw`, `jaw`, `hand`) or joint channels                         |
| Motion        | All non-gripper state channels; speed and path use only these                                                                                         |
| Gripper       | `choose_grippers()` keeps the widest-ranging channel of each gripper group                                                                            |
| Cameras       | Features with dtype `video`; `image` features stored in the tables are noted and not previewed                                                        |
| Capture clock | `detect_clock()` picks the first single-value feature whose name contains `time`, `stamp`, or `clock` and increases in 99 % of steps within episodes  |

Unnamed state or action vectors pair by index, and the report says so. Every choice is written to `layout_notes` in `metrics.json` and appears in the method section. When the layout is wrong for a robot, pass `--state`, `--action`, or `--clock` before adapting the code.

`detect_clock()` infers the unit from the median step against the nominal frame period, rounded to a power of 1000 (seconds, milliseconds, microseconds, or nanoseconds). A clock that fails the monotonic test is skipped; with `--clock` it is an error. Use `--no-clock` for datasets whose timestamp-like columns are not capture times.

## Clocks

A LeRobot v3 export can carry two clocks, and most timing mistakes come from mixing them.

| Clock         | Source                          | Meaning                                                                          |
|---------------|---------------------------------|----------------------------------------------------------------------------------|
| Export clock  | `timestamp`                     | Usually rebuilt as `frame_index / fps`; a perfect grid that hides dropped frames |
| Capture clock | A detected or `--clock` feature | Time the recorder captured the row; use it for gap sizes and wall-clock times    |

Video time equals export time: frame `k` of an episode clip plays at `k / fps`. Place anything a viewer scrubs (ticks, markers, notes) on the export clock, and compute anything physical (gap sizes, wall-clock start times) on the capture clock. Speeds use frame steps times `fps`, so they are per export second.

## Integrity

`analyze_episode()` and `summarize()` record structural checks per episode and in total.

| Check              | Rule                                                                                   |
|--------------------|----------------------------------------------------------------------------------------|
| Frame index        | `frame_index` equals `0 .. n - 1`                                                      |
| Export grid        | Largest difference between `timestamp` and `frame_index / fps` within 0.5 ms           |
| Row count          | Data rows equal `length` in `meta/episodes`                                            |
| First state row    | Rows 0 and 1 of the state are identical, so the state starts one tick late             |
| State update ratio | Share of frames where any state value changes; below 50 % is reported                  |
| State stall        | State frozen for 0.5 s or more while the action keeps changing                         |
| Non-finite values  | NaN or infinite values in any floating-point feature                                   |
| Constant channels  | Channels that never change anywhere, or never change within any episode                |
| Video windows      | Declared video file exists inside the dataset root and its window covers the data rows |

A stall means a sensor or driver stopped updating while commands continued; a robot at rest changes neither stream. `load_episode_meta()` resolves every video path and rejects paths outside the dataset root.

## Motion

Speed is the norm of the frame-to-frame change of the motion channels times `fps`, smoothed with a five-frame moving average. Units are state units per second, because joint units are not known.

| Metric             | Rule                                                                            |
|--------------------|---------------------------------------------------------------------------------|
| Idle speed         | 5 % of the 95th percentile of all nonzero smoothed speeds in the dataset        |
| Idle start and end | Time before the smoothed speed first exceeds idle speed, and after it last does |
| Idle share         | Share of frames at or below idle speed                                          |
| Pause              | Idle for 0.5 s or more between the first and last moving frame                  |
| Path               | Sum of the Euclidean frame-to-frame changes of the motion channels              |
| Peak speed         | Largest smoothed speed                                                          |

Taking the percentile over moving frames keeps the threshold meaningful for datasets that are mostly still, where a percentile over all frames collapses to zero.

## Capture Gaps

A capture gap is a capture-clock interval above 1.5 nominal frame periods between consecutive rows (`GAP_FACTOR`). Each gap records the frame after it, the interval, and `round(interval / period) - 1` missing frames.

| Measure         | Rule                                                                                |
|-----------------|-------------------------------------------------------------------------------------|
| Measured period | Median of intervals between 0.5 and 1.5 periods; jitter is their standard deviation |
| Burst           | An episode with three or more gaps                                                  |
| Catch-up        | An interval shorter than half a period, typical after a late delivery               |
| Non-increasing  | An interval of zero or less; the clock repeated or ran backwards                    |
| Missing share   | Missing frames over recorded plus missing frames                                    |

Confirm a gap before naming its cause.

1. Compare the interval with whole multiples of the measured period. Intervals close to whole multiples mean frames were skipped on a steady cadence; a long interval followed by a catch-up interval means late delivery instead.
2. Check that rows went missing, not only images. Compare the state step across the gap with the median step around it; a ratio near the number of missing periods shows that time passed and the samples of those instants are absent.
3. Look for structure: bursts within one episode, gaps at one task phase, or gaps at one wall-clock time. The episode start in UTC (`start_utc`, when the clock holds epoch time) lets the recording host's logs be checked.

Word the cause as a hypothesis unless host logs confirm it. The data can show that frames were dropped rather than delayed; it cannot show why the recorder fell behind.

## Synchronization

Two estimates relate the streams in one row.

| Estimate      | Method                                                                                                                                             |
|---------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| Command lag   | `best_lag()` shifts the action against the state by 0 to 15 frames per group and keeps the shift with the lowest RMS difference                    |
| Camera offset | `lag_frames()` cross-correlates image motion (mean absolute luma change) with joint speed over -8 to 8 frames and refines the peak with a parabola |

A positive camera offset means the video trails the state. An estimate at the edge of the search window is discarded rather than reported as the window limit. When the error at the best command lag is much lower than at zero shift, most of the difference is delay, not tracking inaccuracy. The report flags a command lag of three frames or more.

Camera offsets are indicative: image motion responds to anything that moves in view, and without per-frame capture times the estimate cannot separate recorder latency from exposure timing. Recommend a physical synchronization test before relying on sub-frame alignment.

## Gripper Events

`gripper_events()` works on the chosen gripper channel.

1. The start value is the median of the first three frames.
2. A frame is away from the start when it differs by more than 20 % of the channel's 1st-to-99th-percentile range (`EVENT_FRACTION`).
3. Close is the first frame of the first away run of at least three frames; open is the first frame of the first return run of at least three frames after it.
4. Cycles count away runs; more than one cycle is a regrasp.

The rule assumes the gripper starts open and has no sign convention, so it works for grippers that close toward either end of their range. Episodes where the gripper never leaves its start value are reported as never closing.

## Start Conditions

`start_conditions()` groups episodes by their first state.

* Two starts share a condition when every channel differs by at most 2 % of that channel's range across the dataset (`START_TOLERANCE`), assigned greedily in recording order.
* Groups of three or more are repeats. With five or more episodes, a repeat that covers half or more of them becomes a finding, because a policy trained on it sees one start condition and near-identical episodes can fall on both sides of a split.
* The chart projects range-scaled start states onto their first two principal components; the axis labels give each component's share of variance.

## Outliers

`flag_outliers()` screens duration, joint path, peak speed, idle share, and mean tracking error with a robust z-score, `0.6745 * (x - median) / MAD`.

* An episode is flagged when the score exceeds 3.5 and the value differs from the median by at least 10 % (`OUTLIER_MIN_EFFECT`).
* Screening needs five or more episodes with a finite value.
* The effect rule stops near-identical datasets, where the MAD is tiny, from flagging meaningless differences.

Every flag names one metric, its value, and its score, so each is explainable.

## Video Screens

`media.py` decodes every camera window once at clip resolution and keeps per-frame statistics in `CameraScreen`.

| Screen               | Rule                                                                                  |
|----------------------|---------------------------------------------------------------------------------------|
| Frame count          | Decoded frames against data rows: `shortfall` when fewer, `extra` when more           |
| Brightness           | Mean and 5th-percentile luma                                                          |
| Black frames         | Mean luma below 12                                                                    |
| Repeated frames      | Largest per-pixel difference to the previous frame of 1 or less                       |
| Repeats while moving | Repeated frames while the joints move faster than idle speed; only these are reported |
| Sharpness            | Median Laplacian magnitude of the luma                                                |

Static scenes legitimately repeat frames, which is why only repeats during motion become a finding. Confirm black or repeated frames by eye before naming a cause; lens caps, lighting, and exposure changes look alike to these screens.

## Exploration Workflow

Confirm candidates before writing them up.

1. Write scratch analysis to a file outside the dataset, the work folders, and the skill, and run it with the same interpreter.
2. Watch the episode at the flagged moment in the report player, and step frame by frame.
3. Extract a full-resolution frame with input seeking. For frame `k` of a window that starts at `from` seconds in its file, seek to `from + (k + 0.5) / fps`.
4. Record what the frame shows beside the metric that flagged it.

## Wording Findings

Rank findings by what they cost a training run.

| Severity | Examples                                                                                                                                                  |
|----------|-----------------------------------------------------------------------------------------------------------------------------------------------------------|
| High     | Video missing for half or more of the episodes; state stalls; capture-gap bursts or more than 0.5 % missing frames                                        |
| Medium   | Missing video in fewer episodes; other capture gaps; video and data disagree; one dominant start pose; command lag; outliers; a gripper that never closes |
| Low      | Regrasps; constant channels; late or slow state updates; idle heads and tails; frozen or black frames                                                     |

Every finding states the episodes, the time, the size of the effect, and a playable moment. Say what the data shows ("frames were dropped, not delayed") before what it suggests ("the recorder could not keep up"), and label heuristic screens as indicative.
