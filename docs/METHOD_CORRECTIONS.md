# Final statistical implementation correction

The frozen method protocol defined a connected-component bootstrap for the
candidate-control match graph. A final dependency audit identified that the
same physical vessel-pair/day could appear in both candidate and control roles.
Role-prefixed nodes could therefore split observations that share the same
physical pair/day across resampled components.

The released implementation corrects this by defining every dependency node as:

```text
(UTC date, min(vessel_a, vessel_b), max(vessel_a, vessel_b))
```

The same node identity is used regardless of candidate or control role.
Connected components are formed from these physical vessel-pair/day nodes and
matched-set edges, then sampled with replacement using 2,000 iterations and seed
`20260722`. This preserves repeated and cross-role use of a physical pair/day
within one dependency component.

The correction changes confidence intervals only. It does not change matched
sample sizes, future-entry rates, risk differences, lift point estimates, or
the candidate-screening claim boundary.

The sanitized Web summaries contain the corrected method metadata and
confidence intervals for both study waterways.
