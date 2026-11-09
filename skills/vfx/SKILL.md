---
name: vfx
description: >
  Niagara VFX authoring with niagara_export / niagara_apply: read a whole system as JSON, then create or edit
  emitters, module stacks, inputs (constants, links, random ranges, curves, dynamic inputs), renderers, User
  parameters and event handlers in one atomic, compiled, rollback-safe call. Includes a recipe library (fire, smoke,
  sparks, rain, snow, explosion, magic aura, muzzle flash, steam vent, dust motes, fireworks, neon flicker) and tuning
  rules for "bigger, slower, bluer, denser". Use for any particle effect.
---

# VFX (Niagara)

Tools: `niagara_export` (read), `niagara_apply` (edit, atomic), `niagara_edit` (place in the level, set User
parameters on the placed component), `niagara_particles` (live counts and particle bounds), `pie_test` with
`{"type":"niagara", ...}` assertions.

## Loop: observe, edit, verify
1. **Start from a template** emitter, not from nothing: `add_emitter` with `from` = `Fountain` (continuous upward spray),
   `OmnidirectionalBurst` / `DirectionalBurst` / `SimpleSpriteBurst` (bursts), `HangingParticulates` (box volume, slow
   drift: dust, snow, rain base), `LocationBasedRibbon` (trails), `StaticBeam` / `DynamicBeam`, `UpwardMeshBurst`,
   `ConfettiBurst`, `SingleLoopingParticle`, `Minimal` (just Initialize + State). Copies are standalone (no parent).
2. **Export before you edit**: `niagara_export(system, only_modified=true)` is compact. Every input shows `name`, `type`,
   `mode` and its value; enum inputs list `options`. Use those exact names. Inputs hidden by a static switch aren't listed
   (pass `include_hidden=true`). Switch first, then set the inputs it reveals; one `set_inputs` does that ordering for you.
3. **Apply** one batch. If anything fails, nothing is kept. `compile.errors` / `compile.issues` tell you what is wrong.
   Use `dry_run=true` to validate a risky batch.
4. **Place and verify**: `niagara_edit` `spawn` places it. Then run `pie_test` with
   `{"type":"niagara","actor":"Fire","emitter":"Flames","op":">=","value":20}`, or `niagara_particles(actor=..., samples=10)`.

## Stages and what goes where
`system_spawn` / `system_update` (no `emitter`), `emitter_spawn`, `emitter_update` (EmitterState, SpawnRate, bursts),
`particle_spawn` (InitializeParticle, ShapeLocation, AddVelocity), `particle_update` (forces, then Collision, then
SolveForcesAndVelocity, which runs once per tick, then color and size), `event:<n>` (event handlers).

## Input values
`5`, `[x,y,z]`, `[r,g,b,a]` (HDR above 1 glows), `true`, `"Enum Option"` are constants. `{"link":"User.Tint"}` /
`{"link":"Particles.NormalizedAge"}` links a parameter. `{"random":[min,max]}` is a uniform range of the input's type.
`{"curve":[[t,v...],...]}` is a curve over the particle's normalized age: one value for a float, three for vec3, four
for a color. Curve-typed inputs (`di:NiagaraDataInterfaceCurve`) take the keys directly. Also
`{"dynamic":"RandomRangeFloat","inputs":{...}}`, `{"expression":"..."}` (last resort), `{"object":"/Game/SM_X"}`,
`{"reset":true}`. A dynamic input's own inputs use the path form `"Velocity Speed/Maximum"` in `set_input`.

## Verified input names (UE 5.8 /Niagara modules)
- `SpawnRate`: **SpawnRate**. `SpawnBurst_Instantaneous`: **Spawn Count**, Spawn Time, Loop Count Limit.
- `EmitterState` (templates use Self life cycle): **Loop Behavior** (Once / Multiple / Infinite), **Loop Duration**, Loop Delay.
- `InitializeParticle`: in templates the lifetime is random, so use **Lifetime Min** / **Lifetime Max**. Minimal uses
  **Lifetime** (direct). Also **Color** (shown when Color Mode is direct set, as in Fountain and Burst), **Uniform Sprite
  Size Min/Max**, Mass Min/Max, Position Offset, Sprite Rotation Mode.
- `ShapeLocation`: **Shape Primitive**, **Sphere Radius** (default sphere), **Box Size** (HangingParticulates uses a box).
- `AddVelocity` (Fountain, cone mode): **Velocity Speed** (a RandomRangeFloat), **Cone Axis**, **Cone Angle**. In default
  (linear) mode, as added fresh: **Velocity** (vec3).
- `GravityForce`: **Gravity**. `Drag`: **Drag**. `CurlNoiseForce`: **Noise Strength**, **Noise Frequency**.
  `WindForce`: **Wind Speed**. `VortexForce`: **Vortex Force Amount**, Vortex Axis. `PointAttractionForce`:
  **Attraction Strength**, Attraction Radius.
- `Collision`: CPU Collision Type, Collision Enabled, **Restitution**, Particle Radius Scale.
- `ScaleColor`: **Scale RGB** (vec3), **Scale Alpha** (a FloatFromCurve fade in templates). `Color`: **Color**, Scale Color, Scale Alpha.
- `ScaleSpriteSize`: **Uniform Curve Sprite Scale** (a curve over age). `SpriteRotationRate`: **Rotation Rate**.
- `GenerateDeathEvent`: Event Probability. `ReceiveDeathEvent` goes in the handler's `event:<n>` stage.
- Renderers: `add_renderer` `sprite|mesh|ribbon|light`. Light properties: `RadiusScale`, `ColorAdd`. Sprite: `Alignment`
  (`VelocityAligned` for streaks), `SubImageSize`. Bindings: `ColorBinding`, `SpriteSizeBinding`, ... (see export).

## Recipes
Each recipe is an op list for `niagara_apply(system, create_from="empty", operations=[...])`. Then place it with
`niagara_edit`. `E` below stands for the emitter name and `PS` / `PU` / `EU` for particle spawn / particle update /
emitter update. Abbreviated as `set(E, stage, module, {inputs})` = `{"op":"set_inputs","emitter":E,"stage":...,"module":...,"inputs":{...}}`.

**Fire** (Fountain): `add_emitter Flames from Fountain`; `set(EU SpawnRate {"SpawnRate":120})`;
`set(PS InitializeParticle {"Lifetime Min":0.5,"Lifetime Max":0.9,"Color":[1,0.35,0.05,1],"Uniform Sprite Size Min":20,"Uniform Sprite Size Max":40})`;
`set(PS ShapeLocation {"Sphere Radius":25})`; `set(PS AddVelocity {"Velocity Speed":{"random":[80,160]},"Cone Angle":12})`;
`set(PU GravityForce {"Gravity":[0,0,120]})` (buoyancy); `set_input PU ScaleColor "Scale RGB" curve [[0,1,1,1],[1,0.3,0.05,0]]`;
`add_renderer light {"properties":{"RadiusScale":1.5}}`. Full reference: `bench/tasks/vfx-campfire.json`.

**Smoke** (Fountain): SpawnRate 12; Lifetime 3 to 5; Color `[0.12,0.12,0.12,0.5]`; Uniform Sprite Size 60 to 110; Velocity
Speed random 40 to 90, Cone Angle 15; Gravity `[0,0,20]`; `add_module PU WindForce {"Wind Speed":[30,0,0]}` at index 2;
`add_module PU ScaleSpriteSize {"Uniform Curve Sprite Scale":{"curve":[[0,1],[1,3]]}}`.

**Sparks** (OmnidirectionalBurst): `set(EU EmitterState {"Loop Behavior":"Infinite","Loop Duration":1.0})`;
`set(EU SpawnBurst_Instantaneous {"Spawn Count":60})`; Lifetime 0.25 to 0.5; Color `[6,3,0.8,1]` (HDR); size 1.5 to 3;
Velocity Speed random 300 to 700; `set_renderer sprite {"properties":{"Alignment":"VelocityAligned"}}`. Reference: `vfx-sparks-timer.json`.

**Rain** (HangingParticulates): SpawnRate 400; Lifetime 2; Color `[0.6,0.7,0.9,0.6]`; size 1 to 2;
`set(PS ShapeLocation {"Box Size":[1200,1200,10]})`; remove `WindForce` and `AerodynamicDrag`; `add_module PU GravityForce`
at index 1; `add_module PU Collision {"Restitution":0.05}` at index 2 (keep the emitter on CPU: CPU collision ray-traces
the scene and works offscreen); sprite `VelocityAligned`. Place it about 800 above the ground. Reference: `vfx-rain-collision.json`.

**Snow**: Rain, but SpawnRate 150, Lifetime 6 to 8, size 2 to 4, white; keep `WindForce` (Wind Speed `[40,10,0]`) and
`AerodynamicDrag`; add `GravityForce` `[0,0,-60]`; no velocity alignment; optional `Collision` with Restitution 0.

**Explosion**: `create_from="template:SimpleExplosion"` gives a flash, debris and smoke set. Or build it from
OmnidirectionalBurst: Loop Behavior Once, Spawn Count 200, Velocity Speed 600 to 1400, Lifetime 0.4 to 1.2, Color
`[8,3,0.6,1]`, Drag 2; plus a second Burst emitter for smoke (Spawn Count 20, size 80 to 160, Lifetime 2 to 3, gray, Gravity
`[0,0,40]`) and a `light` renderer on the flash (RadiusScale 4).

**Magic aura** (Fountain): SpawnRate 60; Lifetime 1.5 to 2.5; Color `[0.4,0.6,4,1]`; `set(PS ShapeLocation {"Sphere Radius":60})`;
Velocity Speed 10 to 30; Gravity `[0,0,40]`; `add_module PU VortexForce {"Vortex Force Amount":200}` at index 2;
`add_module PU CurlNoiseForce {"Noise Strength":60,"Noise Frequency":20}`. Pulse it by linking Color to `User.Tint` and animating the User parameter.

**Muzzle flash** (DirectionalBurst): Loop Behavior Once; Spawn Count 12; Lifetime 0.05 to 0.1; Color `[10,6,2,1]`;
Velocity Speed 600 to 900 along Cone Axis `[1,0,0]`; size 10 to 25; `add_renderer light {"properties":{"RadiusScale":3}}`.
Keep `local_space: true` so it follows the gun.

**Steam vent** (Fountain): SpawnRate 30; Lifetime 2 to 3; Color `[0.9,0.9,0.92,0.35]`; size 30 to 50; Sphere Radius 20;
Velocity Speed 120 to 200, Cone Angle 8; Gravity `[0,0,0]`; Drag 0.6; `add_module PU ScaleSpriteSize
{"Uniform Curve Sprite Scale":{"curve":[[0,1],[1,4]]}}`. Reference: `vfx-steam-vent.json`.

**Dust motes** (HangingParticulates as is): SpawnRate 20; Lifetime 6 to 10; size 0.5 to 1.5; Color `[1,0.95,0.8,0.3]`;
Box Size the size of the room; `set(PU WindForce {"Wind Speed":[5,0,2]})`.

**Fireworks**: Rocket (Fountain: SpawnRate 2, Lifetime 1.0 to 1.2, Velocity Speed 1000 to 1300, Cone Angle 6,
`add_module PU GenerateDeathEvent`) + Burst (Fountain with `SpawnRate` removed:
`add_event_handler {"emitter":"Burst","source_emitter":"Rocket","event":"DeathEvent","spawn_number":80}`,
`add_module event:0 ReceiveDeathEvent`, Color `{"random":[[0.5,0.1,0.1,1],[6,4,6,1]]}`, Velocity Speed 200 to 450, Cone
Angle 180, Drag 1). Both must stay on CPU, because events are CPU-only. Reference: `vfx-fireworks.json`.

**Neon sign flicker**: a mesh or light emitter isn't needed. Expose `User.Flicker` (float) and drive it, or use
SingleLoopingParticle: `add_renderer light`, Color `[0.2,1,4,1]`, and on `PU Color` set
`"Scale Alpha": {"curve":{"keys":[[0,1],[0.1,0.2],[0.15,1],[0.6,1],[0.62,0.1],[0.65,1],[1,1]],"interp":"constant"}}`
with Lifetime 1.5 (the curve loops every particle life). Bind the material's emissive to particle color.

## Tuning: what each request changes
- **Bigger**: Uniform Sprite Size Min/Max ×k, and the spawn shape (Sphere Radius / Box Size) ×k. For the *height* of a
  plume, the reach is about rise speed × lifetime: double Velocity Speed (min and max) **or** Lifetime, not both.
- **Smaller / tighter**: the reverse, and lower Cone Angle.
- **Slower**: Velocity Speed ×0.5, Lifetime ×2 (keeps the distance travelled), Drag up. For drifting effects, halve
  Wind Speed, Noise Strength and gravity.
- **Faster / more energetic**: Velocity Speed ×2, Lifetime ×0.5, SpawnRate ×1.5.
- **Denser / more**: SpawnRate (or Spawn Count) ×2. Thinner: ×0.5. Watch the total, (rate × lifetime) live particles:
  keep CPU emitters under about 2,000.
- **Bluer / any hue**: set InitializeParticle **Color** (for example `[0.1,0.35,1,1]`), and check `ScaleColor` /
  `Color` curves, which multiply it. A hue shift on white does nothing. **Brighter / glowing**: color components above 1 (HDR).
- **Longer-lasting**: Lifetime Min/Max ×k. Shorter: the reverse. A fade-out comes from Scale Alpha's curve.
- **Windier / more turbulent**: add `CurlNoiseForce` (Noise Strength 50 to 300) or `WindForce`.
- **Follows the actor**: `set_emitter local_space:true`. Otherwise keep world space (trails, smoke).
- **Burst once / loop**: EmitterState Loop Behavior Once / Infinite, and Loop Duration for the repeat period.
- **Exposed to designers**: `add_user_parameter` plus `{"link":"User.X"}` on the input. Set it per placed actor with
  `niagara_edit set`.

## Gotchas
- Forces go **before** `SolveForcesAndVelocity`, so pass `index` to `add_module` (export shows indices). Collision goes after
  the forces and before the solve.
- Events, light renderers and CPU collision need **CPU** emitters (`set_emitter sim_target:"cpu"`).
- Fixed bounds (`set_bounds` / `set_emitter bounds`) are needed for GPU emitters and are cheaper. Dynamic bounds are CPU only.
- A module copied from a parent emitter (`inherit:true`) can't be removed or moved; disable it with `enable_module false`.
- Names in `set_inputs` match case-insensitively and ignore spaces, but stick to the export's names.
