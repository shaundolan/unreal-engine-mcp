# UE-Bench: baseline (no agent)

**0/15 passed** (0%), 0 skipped

| Domain | Passed | Scored |
|---|---|---|
| assets | 0 | 1 |
| cinematics | 0 | 1 |
| gameplay | 0 | 1 |
| input | 0 | 1 |
| materials | 0 | 3 |
| modeling | 0 | 1 |
| performance | 0 | 1 |
| scene | 0 | 6 |

| Task | Status | Notes |
|---|---|---|
| asset-organize | failed | scene: asset_exists actual=False; scene: asset_exists actual=False |
| cine-intro-camera | failed | scene: asset_exists actual=False; scene: 4 s long actual=None error=LookupError: asset '/Game/UEBench/cine-intro-camera/LS_Intro' not found |
| gameplay-playable-start | failed | scene: actor_exists actual=0; scene: ground under PlayerStart actual=False |
| input-wasd | failed | scene: asset_exists actual=False; scene: asset_exists actual=False |
| material-apply | failed | scene: M_Red applied actual=False |
| material-emissive | failed | scene: asset_exists actual=False; scene: Tint param actual=None error=LookupError: asset '/Game/UEBench/material-emissive/M_Glow' not found |
| material-instance-red | failed | scene: asset_exists actual=False; scene: parent actual=None error=LookupError: asset '/Game/UEBench/material-instance-red/MI_Red' not found |
| modeling-blender-crate | failed | scene: asset_exists actual=False; scene: 3 crates placed actual=False |
| perf-shadow-budget | failed | scene: <= 2 movable shadow casters actual=False |
| scene-cleanup | failed | scene: no debris actual=False; scene: nothing else deleted actual=False |
| scene-greybox-arena | failed | scene: floor 4000x4000 actual=False; scene: 4 tall walls actual=False |
| scene-light-ring | failed | scene: 8 point lights actual=0; scene: evenly spaced actual=None error=ValueError: min() arg is an empty sequence |
| scene-pillar-grid | failed | scene: 25 pillars actual=False; scene: x spacing actual=False |
| scene-raise-crates | failed | scene: z = 50 actual=False |
| scene-tag-enemies | failed | scene: tagged actual=False; scene: filed actual=False |
