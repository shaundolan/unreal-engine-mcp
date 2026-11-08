import sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

src, out = sys.argv[1], sys.argv[2]
base = Image.open(src).convert("RGBA")
W, H = base.size
F = "/usr/share/fonts/truetype/dejavu/"
sans_b = lambda s: ImageFont.truetype(F + "DejaVuSans-Bold.ttf", s)
sans = lambda s: ImageFont.truetype(F + "DejaVuSans.ttf", s)
mono = lambda s: ImageFont.truetype(F + "DejaVuSansMono.ttf", s)
mono_b = lambda s: ImageFont.truetype(F + "DejaVuSansMono-Bold.ttf", s)

ov = Image.new("RGBA", base.size, (0, 0, 0, 0))
d = ImageDraw.Draw(ov)

# --- right-hand agent panel (frosted) --------------------------------------------------------
PX, PY, PW, PH = W - 600, 36, 564, 880
region = base.crop((PX, PY, PX + PW, PY + PH)).filter(ImageFilter.GaussianBlur(18))
base.paste(region, (PX, PY))
d.rounded_rectangle((PX, PY, PX + PW, PY + PH), 18, fill=(10, 12, 20, 205), outline=(255, 255, 255, 40), width=1)

x, y = PX + 28, PY + 24
d.text((x, y), "AGENT  ·  Game Developer", font=sans_b(15), fill=(140, 170, 255, 255)); y += 32
d.text((x, y), "Prompt", font=sans_b(14), fill=(170, 175, 190, 255)); y += 24
for line in ["“Third-person arena at dusk. Collect 9 coins,",
             " dodge 3 patrolling enemies, a health pickup,",
             " a raised platform. Make it playable.”"]:
    d.text((x, y), line, font=sans(17), fill=(240, 240, 245, 255)); y += 25
y += 16
d.line((x, y, PX + PW - 28, y), fill=(255, 255, 255, 35)); y += 18

steps = [
    ("blender_run", "crate · coin · pickup · capsule meshes", "→ /Game/Arena/Props"),
    ("scene_compose", "floor, walls, 8 pillars, dais, ramp", "1 undo step · 143 actors"),
    ("material_edit", "M_Energy, M_Coin (emissive params)", "+ 6 instances"),
    ("enhanced_input", "IA_Move · IA_Look · IA_Jump · IMC", "WASD + gamepad"),
    ("game_setup", "BP_ArenaGameMode · BP_Hero", "level override"),
    ("sequence_edit", "LS_Intro: 4 s dolly + camera cut", ""),
    ("performance_audit", "26.8k tris · within budget", "no issues"),
]
for tool, what, note in steps:
    d.ellipse((x, y + 6, x + 10, y + 16), fill=(90, 220, 140, 255))
    d.text((x + 22, y), tool, font=mono_b(16), fill=(255, 210, 120, 255))
    y += 23
    d.text((x + 22, y), what, font=sans(14), fill=(215, 218, 228, 255))
    if note:
        y += 20
        d.text((x + 22, y), note, font=sans(13), fill=(140, 145, 160, 255))
    y += 28

y += 2
d.line((x, y, PX + PW - 28, y), fill=(255, 255, 255, 35)); y += 18
d.text((x, y), "pie_test", font=mono_b(17), fill=(255, 210, 120, 255))
d.text((x + 100, y + 1), "the definition of done", font=sans(14), fill=(140, 145, 160, 255)); y += 32
checks = ["player spawns on the dais", "9 coins · 3 enemies · 1 health pickup",
          "hold W 2 s → player moved ≥ 400 uu", "jump → z rises, lands on floor",
          "no errors · no “Accessed None”"]
for c in checks:
    d.text((x, y), "✓", font=sans_b(17), fill=(90, 220, 140, 255))
    d.text((x + 26, y + 1), c, font=sans(15), fill=(225, 228, 236, 255)); y += 27

# --- top-left title ------------------------------------------------------------------------------
d.rounded_rectangle((36, 36, 470, 118), 16, fill=(10, 12, 20, 190))
d.text((60, 50), "UnrealMCP", font=sans_b(30), fill=(255, 255, 255, 255))
d.text((256, 61), "prompt → playable", font=sans(19), fill=(255, 190, 110, 255))
d.text((60, 90), "local · open source · built by Nerve agents", font=sans(14), fill=(160, 165, 180, 255))

# --- callouts on the scene ---------------------------------------------------------------------
def tag(px, py, text, col, left=False):
    tw = d.textlength(text, font=sans_b(13))
    if left:
        d.line((px, py, px - 40, py - 46), fill=col + (220,), width=2)
        d.ellipse((px - 5, py - 5, px + 5, py + 5), fill=col + (255,))
        bx = px - 40 - tw - 14
        d.rounded_rectangle((bx, py - 76, px - 32, py - 46), 8, fill=(10, 12, 20, 210))
        d.text((bx + 9, py - 70), text, font=sans_b(13), fill=col + (255,))
        return
    d.line((px, py, px, py - 46), fill=col + (220,), width=2)
    d.ellipse((px - 5, py - 5, px + 5, py + 5), fill=col + (255,))
    d.rounded_rectangle((px - 8, py - 76, px + tw + 14, py - 46), 8, fill=(10, 12, 20, 210))
    d.text((px + 3, py - 70), text, font=sans_b(13), fill=col + (255,))
tag(952, 470, "BP_Hero", (120, 170, 255))
tag(870, 655, "BP_Enemy · patrol AI", (255, 110, 90))
tag(1060, 470, "BP_Coin ×9", (255, 210, 90))
tag(1276, 590, "BP_HealthPickup", (90, 230, 140), left=True)
tag(905, 870, "SM_Crate (Blender)", (230, 190, 140))

# --- footer: honesty line -------------------------------------------------------------------------
d.rectangle((0, H - 64, W, H), fill=(6, 7, 12, 230))
d.text((36, H - 44), "Concept render. Every mesh here was built with UnrealMCP's blender_run helpers on a GTX 1080 "
       "(143 meshes, 26.8k triangles, Cycles, 41 s). The target is this scene inside Unreal, "
       "with pie_test as the gate.", font=sans(15), fill=(175, 180, 195, 255))

Image.alpha_composite(base, ov).convert("RGB").save(out, quality=95)
print(out)
