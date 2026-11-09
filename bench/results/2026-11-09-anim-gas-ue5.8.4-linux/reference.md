# UE-Bench: animation + GAS tasks, reference solutions, UE 5.8.4 Linux, GTX 1080, offscreen

Branch `feat/anim-gas` (plugin built from this branch), sample project with GameplayAbilities enabled and the template
Mannequin installed (`python scripts/install_mannequin.py`). Editor on port 55563.

**4/4 passed** with `--agent solution` (twice in a row, so the sandbox reset works for these assets too);
**0/4** with `--agent none`.

| Task | Solution | Baseline |
|---|---|---|
| anim-attack-montage | passed | failed |
| anim-blendspace-locomotion | passed | failed |
| anim-locomotion-abp | passed | failed |
| gas-heal-effect | passed | failed |

bp_apply regression after the shared-machinery refactor: blueprint-coin-pickup, blueprint-greeter, ui-hud,
ai-guard-patrol passed live (PIE included).

Extra live checks (not bench tasks):
- PIE: a Character using the generated ABP_Hero, dropped from 1500 units: its anim instance is ABP_Hero_C and the
  event graph sets IsInAir = true and Speed > 10 while falling.
- Montage with two segments, an extra UpperBody slot, sections A1 → A2 → A1 (loop), a named notify, a notify state;
  AnimNotify_PlayMontageNotify keeps its NotifyName.
- 2D BlendSpace from a SkeletalMesh path; sequence rate scale / root lock / curve / notify add + remove.
- IK Rigs for Manny and Quinn (auto chains, retarget root pelvis), IK Retargeter (5 ops), batch retarget of MM_Idle.
- AnimBP rule `{"all":[Speed < 3, Armed]}` and an automatic transition compile clean; a bad transition rolls the
  created AnimBP back (asset gone).
- gameplay_tags add/list; GE with duration 5 s, period 1 s, asset + granted tags (set through property_set on the
  GE components); a cooldown GE whose granted tags satisfy the ability compiler's cooldown validation; GA with an
  ActivateAbility → BP_ApplyGameplayEffectToOwner → EndAbility graph; grant_abilities on a Character with an existing
  BeginPlay chain; attribute_set source generation.

Note: right after an editor start the asset registry may still be scanning; a bench run started at that moment can
see leftovers of an earlier session as "already exists". `scripts/ue_linux.sh launch` waits 10 s after the first ping
for this reason.
