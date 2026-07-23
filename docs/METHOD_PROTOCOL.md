# Review-v10 Pre-result Method Protocol

Status: frozen before authoritative review-v10 recomputation
Freeze date: 2026-07-22
Baseline: review-v9 commit `71891c73a5d915d8410e9c57eec53d841e86ecfd`
Applies to: San Francisco Bay (2025-05-01 to 2025-05-07) and Tokyo Bay (2024-08-01 to 2024-08-07)

This protocol is a versioned amendment to the review-v9 protocol. Review-v9 remains
traceable and is not overwritten. Sections 1-3 below retain the review-v9 encounter
state, trajectory-continuity, strict-future, and observability definitions. Sections
4-6 fix the second-round expert-review findings before authoritative review-v10
headline outputs are generated. Thresholds must not be relaxed after viewing results.

## 1. Causal common-time encounter states

- Encounter opportunities are evaluated on an epoch-aligned 60 s grid.
- For each vessel and grid time, the source state is the most recent valid AIS state
  at or before the grid time and no more than 60 s old. It is propagated to the grid
  time under the local constant-ground-track assumption. Future source states are
  never used to construct a screening state.
- A usable course is a segment-derived ground-track course computed only from
  consecutive points in the same track segment. At a segment's first point, San
  Francisco may use a valid native COG fallback; Tokyo Bay waits for a same-segment
  displacement because its source has no native COG field.
- Spatial candidate generation uses a dynamic bucket neighborhood. The final 2 nm
  threshold is applied in the implementation's local nautical-mile tangent-plane
  metric. Indexed and brute-force pair sets must be identical in all audited
  production bins and regression fixtures. This is not described as an exact
  great-circle calculation.

## 2. Strict-future trajectory reconstruction

For a candidate episode, `t0` is the reference time of its first qualifying record
and `t1 = t0 + 15 min`.

- Every position state retains `track_id`.
- Eligible outcome points and interpolation endpoints satisfy `t0 < t <= t1`.
- An interpolation edge requires two endpoints in the same non-empty `track_id`,
  positive elapsed time no longer than 180 s, and no track-segment break.
- No point at or before `t0`, no point after `t1`, and no cross-segment endpoint may
  contribute to an outcome.
- The primary minimum separation is the continuous minimum over overlapping
  same-segment piecewise-linear intervals in a local tangent plane. Fixed 10 s,
  30 s, and 60 s grids remain sensitivity checks.

## 3. Primary observability

The primary rule remains fixed:

1. at least 21 of the 30 scheduled 30 s common samples;
2. at least 630 s of common continuous coverage;
3. no uncovered run longer than 180 s in `(t0, t1]`.

Predicted-time-neighborhood coverage is a separate audit condition for time-error
reporting and is not part of the shared candidate/control observability label.
Primary geometric outcomes are future minimum separation at or below 0.5 nm and
1.0 nm. These remain geometric support outcomes, not incident labels.

## 4. Non-candidate control enrichment analysis

### 4.1 Cohort construction

- Candidate episode anchors remain the first qualifying record of each 15 min
  vessel-pair/day episode so that they match the paper's encounter episode unit.
- Potential controls are non-candidate opportunities. Candidate-window exclusion
  (`+/-15 min`) and control thinning (at least 15 min) operate on each vessel pair's
  continuous timestamp sequence, including across UTC midnight. A day boundary
  cannot preserve an otherwise excluded or duplicate-nearby control.
- Candidate and control anchors use the identical strict-future reconstruction and
  primary observability rule.

### 4.2 Review-v10 primary matched comparison

Matching is deterministic 1:1 without replacement. Exact strata are:

- calendar date;
- 4 h UTC time block;
- 0.05 degree spatial block;
- 0.5 nm current-distance band;
- within-day local pair-opportunity-exposure tertile.

Every accepted match must also satisfy all prespecified calipers:

- absolute reference-time difference no greater than 60 min;
- absolute relative-speed difference no greater than 5 kn;
- absolute closing-speed difference no greater than 2.5 kn.

Within the eligible set, deterministic nearest matching uses normalized absolute
differences in reference time, current distance, relative speed, closing speed,
state skew, and local exposure, followed by stable identifier tie-breaks. No caliper
is relaxed after the result is viewed.

Report the full sample flow, match rate, unmatched counts, candidate-only/control-only/
joint observability, and pre/post-match standardized mean differences for current
distance, time of day, source-state skew, relative speed, closing speed, and local
exposure. DCPA and TCPA define candidate assignment and are reported descriptively
or in calibration plots; they are not balance targets.

Uncertainty uses a fixed-seed bootstrap over connected components of the bipartite
candidate-pair/day to control-pair/day match graph, so repeated pair/day use on
either side remains in the same resampled dependency unit. If this implementation
cannot be completed, matched-set and one-sided pair/day intervals must both be
reported and the remaining dependence limitation stated explicitly.

The primary estimands are candidate versus matched-control future-entry rates,
risk difference, and lift at 0.5 nm and 1.0 nm, conditional on exact strata,
calipers, and joint primary observability. Current-distance-above-0.5-nm and
above-1.0-nm results are sensitivities.

### 4.3 Removed capture claim

The review-v9 values 7,945/9,198 (86.4%) and 10,244/16,487 (62.1%) were computed in
a selected anchor sample after candidate-window exclusion and control thinning.
They are not recall over all 300,904 pair opportunities and are removed from the
review-v10 manuscript headline evidence. Any retained audit output must call them
selected-anchor hit composition and explicitly state that they are not all-
opportunity capture or recall.

Review-v10 does not introduce a new all-opportunity outcome-episode endpoint. Such
an endpoint would require a separately frozen candidate-independent de-duplication
and lead-time design and is deferred rather than improvised before submission.

## 5. Fold-specific fusion-value evaluation

- Compare density-only, behavior-evidence-only, encounter-only, and fused rankings
  at Top 5%, 10%, and 20% review-cell budgets.
- Evaluate primary-supported encounter episodes and de-duplicated corroborated
  behavior track/day evidence separately.
- For every leave-one-day-out fold, only the six training days may determine
  moving-count thresholds, regular traffic cells, dominant ground-track course,
  training-day behavior scores, and the ranking surface.
- Both the six training days and the held-out day are re-scored with that fold's
  six-day traffic-pattern definition. The held-out day may form evaluation targets
  but cannot influence any learned pattern or training score.
- Each fold records its training dates, learned thresholds/cell counts, behavior
  target count, and provenance hash.
- The fusion gate remains: at two or more budgets and in a majority of the seven
  folds, fused must improve behavior-evidence capture over encounter-only while
  retaining at least 95% of encounter-only supported-episode capture.
- If the gate fails after the corrected fold-specific computation, fusion is
  described only as an optional multi-evidence review view. A pass supports only
  internally defined multi-evidence coverage, not operational, safety, incident,
  or analyst-efficiency superiority.

## 6. Causal encounter evidence-card contract

- `prediction_current_distance_nm` is the separation at the causal first candidate
  record `t0`.
- `min_current_distance_nm`, if retained, is explicitly an episode-wide descriptive
  minimum and must never be labeled as current separation at `t0`.
- Card geometry, printed current separation, predicted DCPA/TCPA, and strict-future
  outcome must originate from the same causal record and episode.
- For positive TCPA under the constant-relative-motion calculation,
  `predicted DCPA <= prediction current distance + numerical tolerance`.
- The relative `t0` coordinates and printed current separation must agree within
  the declared plotting/rounding tolerance.

## 7. Reporting and interpretation boundary

- Use `behavior-evidence-only`, `encounter-only`, `fused`, `review-priority cells`,
  `segment-derived ground-track course`, and `geometric support` consistently.
- Do not use prediction accuracy, near-miss, risk probability, enforcement finding,
  COLREG violation, certified collision avoidance, or demonstrated workload
  reduction.
- Tokyo Bay supports only cross-source executability and common output-schema
  generation. It does not support threshold transfer, cross-port performance
  generalization, comparative port safety, or independent operational validation.
