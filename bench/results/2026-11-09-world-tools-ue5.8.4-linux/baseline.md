# UE-Bench: world-* baseline (no agent)

**0/4 passed** (0%), 0 skipped

| Domain | Passed | Scored |
|---|---|---|
| world | 0 | 4 |

| Task | Status | Notes |
|---|---|---|
| world-forest-slopes | failed | tools: >= 150 cone foliage instances, slopes < 25, scale 0.8-1.5 actual='expectation false' |
| world-pcg-forest | failed | tools: graph wires a SurfaceSampler into a StaticMeshSpawner actual=None; tools: volume uses the graph and spawned >= 100 cylinders actual=None |
| world-road-spline | failed | tools: Road starts at TownA, ends at TownB actual=None; tools: >= 3 points on the terrain (not at marker height), >= 10 mesh segments actual=None |
| world-rolling-hills | failed | tools: 4x4+ components, relief >= 800 cm actual="no landscape labelled or named 'Hills'"; tools: no slope over 35 degrees actual="no landscape labelled or named |
