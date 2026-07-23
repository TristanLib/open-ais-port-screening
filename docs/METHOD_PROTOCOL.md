# Review-v9 Frozen Method Protocol

Status: frozen before review-v9 headline recomputation
Freeze date: 2026-07-22
Pre-result amendment A: 2026-07-22, before inspecting strict-future or control headline outputs
Applies to: San Francisco Bay (2025-05-01 to 2025-05-07) and Tokyo Bay (2024-08-01 to 2024-08-07)

This protocol fixes the primary definitions before the corrected headline results are inspected. Later code or document changes may clarify implementation details, but a change to a primary threshold must be versioned, justified, and reported as a deviation rather than silently replacing this protocol.

## 1. Causal common-time encounter states

- Encounter opportunities are evaluated on an epoch-aligned 60 s grid.
- For each vessel and grid time, the source state is the most recent valid AIS state at or before the grid time and no more than 60 s old. The state is propagated forward to the grid time under the same local constant-ground-track assumption used by CPA/TCPA. Future states are never used to construct a screening state.
- A usable course is a segment-derived ground-track course computed only from two consecutive points in the same track segment. At a segment's first point, San Francisco may use a valid native COG fallback; Tokyo Bay has no native COG and therefore waits for the next same-segment point.
- Spatial candidate generation operates on the synchronized grid positions. Bucket-search radii must be calculated dynamically from the 2 nm threshold, bucket size, and the most poleward latitude present. Exact distance filtering follows the index search.
- Regression fixtures at high density, longitude/latitude bucket boundaries, and a 12:00:59/12:01:00 reporting boundary must produce exactly the same within-2-nm vessel-pair set as brute force.

## 2. Strict-future trajectory reconstruction

For a candidate episode, `t0` is the reference time of its first qualifying candidate record and `t1 = t0 + 15 min`.

- Every position state retains `track_id`.
- An exact outcome position is eligible only when `t0 < t <= t1`.
- An interpolation edge is eligible only when both endpoints satisfy `t0 < t <= t1`, both endpoints have the same non-empty `track_id`, the endpoint interval is positive and no longer than 180 s, and the edge does not cross a recorded segment break.
- No point at or before `t0`, no point after `t1`, and no endpoint from another track segment may contribute to an outcome.
- The primary actual minimum separation is solved continuously over all overlapping eligible piecewise-linear intervals of the two vessels in a local tangent plane. This includes a minimum occurring before the first 30 s grid point. Fixed-grid minima at 10 s, 30 s, and 60 s are retained as sensitivity checks only.

## 3. Primary observability rule

The 15 min future window contains 30 scheduled 30 s evaluation times (`t0+30 s` through `t1`). An episode is primary-observable only if all of the following hold:

1. at least 21 of 30 scheduled times have a valid common reconstructed position;
2. the union of common valid continuous intervals covers at least 630 s (70% of the window);
3. the longest uncovered run anywhere in `(t0, t1]`, including leading and trailing gaps, is no longer than 180 s.

Amendment A removed predicted-closest-time coverage from the primary observable label. That condition is label-dependent for non-candidate controls: an otherwise observable control can have undefined, negative, or beyond-window TCPA by definition. Keeping it in the shared label would select controls using the screening outcome. Coverage within the clipped interval `predicted closest time +/- 60 s` is therefore reported separately and is required only for the predicted-versus-observed closest-time error audit. The amendment was recorded before inspecting strict-future or control headline outputs.

The primary support outcomes are future minimum separation at or below 0.5 nm and at or below 1.0 nm among primary-observable episodes. Coverage duration, common sample count, maximum uncovered run, and DCPA absolute error are reported for primary-observable episodes. Predicted-versus-observed closest-time absolute error is reported only where the predicted-time audit window has common coverage.

Observability sensitivity is reported at 50%, 70%, and 90% common-grid coverage (15/30, 21/30, and 27/30 points), while keeping the segment and endpoint rules fixed. Minimum-distance sensitivity is reported for continuous, 10 s, 30 s, and 60 s evaluation.

## 4. Non-candidate geometric controls

- The source population is the complete set of synchronized vessel-pair opportunities that pass the 2 nm current-distance check.
- Candidate anchors use the first qualifying record of each 15 min pair/day episode. Potential control anchors are non-candidate opportunities thinned to at least 15 min separation for the same vessel pair and excluded when the pair has a candidate within +/-15 min.
- The primary matched comparison is deterministic 1:1 matching without replacement on exact date, 4 h UTC block, 0.05 degree spatial block, 0.5 nm current-distance band, and within-day local pair-opportunity-exposure tertile. Unmatched anchors are reported and excluded from the matched estimate; no post-result relaxation is allowed.
- Candidate and control anchors use the identical strict-future reconstruction and observability rule. Outcomes are future entry within 0.5 nm and 1.0 nm.
- Report matched rates, risk difference, lift, capture among all observable opportunity anchors, calibration by predicted DCPA, TCPA, source-state skew, and current distance, and 95% vessel-pair/day cluster-bootstrap intervals with a fixed seed.

These are geometric comparison targets, not accident, near-miss, enforcement, collision-avoidance, or navigation labels.

## 5. Fusion-value evaluation and claim gate

- Compare density-only, corroborated-behavior-only, encounter-only, and fused rankings at Top 5%, 10%, and 20% review-cell budgets.
- Evaluate two targets separately: primary-supported encounter episodes and de-duplicated corroborated behavior track/day evidence.
- Report the two-target Pareto relation, six-day-train/one-day-evaluate leave-one-day-out results, and ranking overlap/stability across held-out days.
- A fused-only evidence case must be auditable and must explain what behavior evidence adds; it remains a review example, not an operational event label.
- Fusion may be described as providing measured incremental ranking value only if, at two or more of the three budgets and in a majority of leave-one-day-out folds, it improves corroborated-behavior capture over encounter-only while retaining at least 95% of encounter-only supported-episode capture. Otherwise it is described as an optional multi-evidence review view and no superiority claim is made.

## 6. Interpretation boundary

All results remain candidate screening and geometric support. Tokyo Bay supports only cross-source executability and common output-schema generation. The analysis does not establish threshold transfer, cross-port performance generalization, port-safety comparison, accident probability, near-miss detection, enforcement findings, or certified collision avoidance.
