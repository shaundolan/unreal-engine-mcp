---
name: gas
description: >
  Gameplay Ability System: gameplay tags, GameplayEffects (instant/duration/periodic, modifiers, tags),
  GameplayAbilities (tags, cost, cooldown, ActivateAbility graph), granting abilities and attribute sets on a
  character, and a generated C++ AttributeSet. Use for health/damage/healing, abilities, buffs and cooldowns.
---

# Gameplay Ability System

Tools: `gameplay_tags`, `gas_edit`, `gas_inspect`; Blueprint logic through `bp_apply`.
Needs the **GameplayAbilities** plugin (off by default). Add `{"Name":"GameplayAbilities","Enabled":true}` to the
.uproject `Plugins` (the sample project has it) and restart. Without it `gas_edit` says so.

## The pieces
- **AttributeSet** (Health, Mana, ...): C++ only. For prototypes the engine ships `AbilitySystemTestAttributeSet`
  (Health, MaxHealth, Mana, MaxMana, Damage, ...). For a real game, generate one (below).
- **AbilitySystemComponent** (ASC) on the character holds attributes, effects and abilities.
- **GameplayEffect** (GE): data. Changes attributes (modifiers), grants tags, lasts instant/for a time/forever.
- **GameplayAbility** (GA): logic. Activated on the ASC; usually applies effects and plays montages.
- **Gameplay tags**: names like `Ability.Heal`. They must be registered; `gas_edit` registers every tag it is given.

## Heal: effect + ability + grant, three ops
```json
gas_edit(operations=[
  {"op":"create_effect","asset":"/Game/GAS/GE_Heal","duration":"instant",
   "modifiers":[{"attribute":"AbilitySystemTestAttributeSet.Health","op":"add","magnitude":25}],
   "asset_tags":["Effect.Heal"]},
  {"op":"create_ability","asset":"/Game/GAS/GA_Heal","ability_tags":["Ability.Heal"],
   "graph":[{"op":"add_node","id":"act","kind":"event","event":"ActivateAbility"},
            {"op":"add_node","id":"apply","kind":"call_function","function":"BP_ApplyGameplayEffectToOwner",
             "class":"GameplayAbility","pins":{"GameplayEffectClass":"/Game/GAS/GE_Heal.GE_Heal_C"}},
            {"op":"add_node","id":"end","kind":"call_function","function":"EndAbility","class":"GameplayAbility"},
            {"op":"connect","from":"act.then","to":"apply.exec"},{"op":"connect","from":"apply.then","to":"end.exec"}]},
  {"op":"grant_abilities","character":"/Game/BP_Hero","abilities":["/Game/GAS/GA_Heal"],
   "attribute_sets":["AbilitySystemTestAttributeSet"]}])
```
`grant_abilities` adds an AbilitySystemComponent named `AbilitySystem` (unless the Blueprint already has one) with
those attribute sets in `DefaultStartingData`, and calls `GiveAbility` on BeginPlay *before* whatever BeginPlay already
did. Activate from input with `bp_apply`: get_var `AbilitySystem` → call `TryActivateAbilityByClass`
(class `AbilitySystemComponent`, pin `InAbilityToActivate` = `/Game/GAS/GA_Heal.GA_Heal_C`).

## Effects
- `"duration"`: `"instant"` (permanent base-value change), `"infinite"` (until removed), or seconds.
- `"period"`: seconds between executions (heal/damage over time; use with a duration or infinite).
- Modifier `"op"`: `add`, `multiply`, `divide`, `override`, `add_final`, `multiply_compound`.
  `"magnitude"`: a number, or `{"set_by_caller":"Data.Amount"}` (set at apply time).
- `"asset_tags"`: tags describing the effect; `"granted_tags"`: tags the target has while it is active.
  A **cooldown** effect must have a duration and `granted_tags` (the engine refuses one without; the error comes
  back from the ability's compile).
- `"stack_limit"`: aggregate stacks by target; `"properties"`: any other class default by C++ name.

## Abilities
`ability_tags`, `activation_owned_tags`, `activation_required_tags`, `activation_blocked_tags`,
`cancel_abilities_with_tag`, `block_abilities_with_tag`, `cost_effect`, `cooldown_effect`, `instancing`
(`instanced_per_actor` default, `instanced_per_execution`, `non_instanced`), `parent` (another GA class).
`"graph"` takes bp_apply ops on the new ability: events `ActivateAbility`, `OnEndAbility`; calls
`CommitAbility`, `EndAbility`, `BP_ApplyGameplayEffectToOwner`, `BP_ApplyGameplayEffectToTarget`.
`PlayMontageAndWait` is a latent ability task (`"kind":"node"` with class `K2Node_LatentAbilityCall`); untested.

## Your own AttributeSet (C++ recipe)
```json
gas_edit(operations=[{"op":"attribute_set","module":"MyGame","name":"HealthSet",
  "attributes":["Health","MaxHealth","Stamina"],"defaults":{"Health":100,"MaxHealth":100},"write":true}])
```
Generates `UHealthSet` (ATTRIBUTE_ACCESSORS, replication with OnRep, Health clamped to MaxHealth in
`PreAttributeChange` / `PostGameplayEffectExecute`), writes `Source/MyGame/{Public,Private}/HealthSet.{h,cpp}`
through `cpp_source`, and adds `GameplayAbilities`, `GameplayTags`, `GameplayTasks` to `MyGame.Build.cs`.
Without `write` it returns the text for review. A new UCLASS needs a full build and an editor restart (Live Coding
cannot add classes): close the editor, build (`scripts/ue_linux.sh build`, or Build.bat/the IDE on Windows), relaunch.
Then use `"HealthSet.Health"` in modifiers and `"attribute_sets":["/Script/MyGame.HealthSet"]` when granting.

## Verify
`gas_inspect(asset)` reads an effect (duration_policy, duration, period, modifiers, asset_tags, granted_tags) or an
ability (tags, cost/cooldown, instancing). In play, `pie_test` with python assertions, e.g. the ASC exists:
`a.get_component_by_class(unreal.AbilitySystemComponent) is not None`.

## Gaps
- Gameplay Cues, executions (`UGameplayEffectExecutionCalculation`), MMCs and AbilityTasks beyond what bp_apply's
  generic `node` kind can place are not wrapped.
- Attribute values in play are not exposed to Python for engine test sets; check effects through tags/behaviour.
