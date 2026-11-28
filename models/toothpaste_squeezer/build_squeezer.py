"""A ratcheting toothpaste squeezer: a frame and a key, no supports, no hardware.

The flat end of the tube goes up through the slot between the frame's two bars and through the window in
the key's shaft. Turning the key winds the tube round the shaft and pulls it through the slot, which
squeezes the paste forward. Two spring fingers on the key click over saw teeth on the rim of the frame's
near plate, so the key turns one way only and the tube cannot unwind.

The frame prints as it is used, slot down. The key prints flat, so its fingers bend along the lines of
plastic. Print in PETG: the fingers flex at every click and the part lives in a wet room.
Run it with text-to-cad's command, from this folder:
    cad server start
    cad run build_squeezer.py        (writes three STEP files into out/ and prints the checks)
    cad server stop
Change a dimension below and run it again. Give out/Squeezer_plate.step to slice_model.
"""
import math

import build123d as bd
from cad import step

TUBE = 62.0           # widest flat end of a tube this takes; not measured from the owner's tube
WINDOW, SLIT = TUBE + 4, 2.5    # the window in the shaft the tube's end goes through
SLOT = 3.0            # between the bars: narrow enough to flatten the tube, wide enough for its crimped end
BAR_W, BAR_H = 7.0, 5.0         # each bar; 7 mm wide so the slot opens under 0.5 mm when the tube is pulled
NEAR, FAR = 6.0, 5.0  # the end plates: near carries the teeth, far only holds the shaft's end
INSIDE = WINDOW + 2   # between the plates
AXIS = 16.0           # shaft centre above the bed: leaves 11 mm round the shaft for the rolled tube
SHAFT, THICK, BORE = 11.0, 8.0, 11.8    # shaft across, the key's thickness (and the shaft's flats), the holes: 0.4 mm clear all round, as a hole lying on its side sags at the top
RIM, TOOTH, TEETH = 18.0, 1.2, 32       # tooth tips' radius, depth, count: 11.25 degrees a click
HOOK = 5.0            # tooth faces lie tangent to a circle this radius, so a loaded finger is pulled in, not out
FINGER, FINGER_CLEAR = 2.2, 0.3         # finger thickness, and its gap over the tooth tips
PAWL_CLEAR = 0.3      # the finger's tooth stops this far above the tooth roots: no standing load, nothing creeps
PAWL_LONG = 2.8       # the finger's tooth, measured round the rim
HANDLE_GAP = 16.0     # plate to handle: with the plate's thickness this is the fingers' 22 mm length
HANDLE, HANDLE_W = 9.0, 56.0
LENGTH = NEAR + INSIDE + FAR    # the frame, end to end

ROOT = RIM - TOOTH
FACE = RIM + FINGER_CLEAR       # the fingers' inner faces, from the shaft's centre
PAWL_TIP = math.sqrt((ROOT + PAWL_CLEAR) ** 2 - (THICK / 2) ** 2)


def prism(points, length, x=0.0):
    """A shape drawn in the plane across the shaft (Y across, Z up) and pulled along the shaft."""
    # dir is given because a mirrored outline runs the other way round and would otherwise be pulled backwards
    return bd.Pos(x, 0, 0) * bd.extrude(bd.Plane.YZ * bd.Polygon(*points, align=None), length, dir=(1, 0, 0))


def near_outline():
    """The near plate, about the shaft's centre: saw teeth round the top three quarters, each face undercut, and
    below them a straight 45 degree run to the bed each side, which prints with no overhang. The straight runs
    start at a tooth tip and pass through the next one, so no tooth gap is left half closed.
    The key turns from +Y towards +Z."""
    back = math.asin(HOOK / RIM) - math.asin(HOOK / ROOT)   # the root sits this far behind its tip
    pitch = 2 * math.pi / TEETH
    lowest = round(TEETH / 8)       # a tip half a tooth above 45 degrees below level, each side
    points = []
    for n in range(-lowest, TEETH // 2 + lowest):
        tip = (n + 0.5) * pitch
        points += [(RIM * math.cos(tip), RIM * math.sin(tip)), (ROOT * math.cos(tip + back), ROOT * math.sin(tip + back))]
    (y, z), points = points[0], points[:-1]     # end on a tip
    foot = y - (AXIS + z)           # where the 45 degree run meets the bed
    return points + [(-foot, -AXIS), (foot, -AXIS)]


def bar_outline(side):
    """One bar. Its slot edge is rounded on top, where the tube bends to the roll, and chamfered underneath."""
    inner, outer, r = SLOT / 2, SLOT / 2 + BAR_W, 1.5
    arc = [(inner + r - r * math.cos(a), BAR_H - r + r * math.sin(a)) for a in (math.radians(d) for d in range(0, 91, 15))]
    return [(side * y, z) for y, z in [(inner + 1, 0), (outer, 0), (outer, BAR_H), *reversed(arc), (inner, 1)]]


def frame_body():
    near = prism(near_outline(), NEAR)
    far = bd.Pos(NEAR + INSIDE + FAR / 2, 0, 0) * (
        bd.Rot(0, 90, 0) * bd.Cylinder(9.5, FAR) + bd.Pos(0, 0, -AXIS / 2) * bd.Box(FAR, 19, AXIS))
    body = bd.Pos(0, 0, AXIS) * (near + far)
    for side in (1, -1):
        body += prism(bar_outline(side), LENGTH)
    body -= bd.Pos(LENGTH / 2, 0, AXIS) * bd.Rot(0, 90, 0) * bd.Cylinder(BORE / 2, LENGTH + 2)
    assert len(body.solids()) == 1 and body.is_valid, "the frame is not one sound solid"
    body.label, body.color = "Squeezer_frame", "#d9d9d9"
    return body


def key_body(lift=0.0):
    """Lying flat as it prints: the shaft along X with its centre at Y 0, Z THICK / 2; X 0 is the plate's outer face.
    lift moves the fingers outwards, as they bend in use; only the checks use it."""
    low = (bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN)
    start, end = -HANDLE_GAP, LENGTH + 1.0
    round_shaft = bd.Pos((start + end) / 2, 0, THICK / 2) * bd.Rot(0, 90, 0) * bd.Cylinder(SHAFT / 2, end - start)
    body = (bd.Pos(start, 0, 0) * bd.Box(end - start, SHAFT, THICK, align=low)) & round_shaft
    body += bd.Pos(start - HANDLE, 0, 0) * bd.Box(HANDLE, HANDLE_W, THICK, align=low)
    body += bd.Pos(-2.2, 0, 0) * bd.Box(1.8, SHAFT + 5, THICK, align=low)       # a shoulder: stops the key going in too far
    body -= bd.Pos(NEAR + 1, 0, 0) * bd.Box(WINDOW, SLIT, THICK, align=low)
    pawl = prism([(FACE + 0.1, 0), (PAWL_TIP, 0), (FACE + 0.1, PAWL_LONG)], NEAR - 1.4, x=0.6)  # its upright face locks, its slope rides the teeth
    finger = bd.Pos(start, FACE + FINGER / 2, 0) * bd.Box(HANDLE_GAP + NEAR - 0.2, FINGER, THICK, align=low) + pawl
    for y in (FACE, FACE + FINGER):     # a rounded root each side, so the bend does not start at a sharp corner
        corner = bd.Box(1.5, 1.5, THICK, align=(bd.Align.MIN, bd.Align.MIN, bd.Align.MIN))
        corner -= bd.Pos(1.5, 1.5, 0) * bd.Cylinder(1.5, THICK, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
        finger += bd.Pos(start, y, 0) * (corner if y > FACE else bd.mirror(corner, about=bd.Plane.XZ))
    finger = bd.Pos(0, lift, 0) * finger
    # the second finger is the first turned half a turn about the shaft, so both lock the same way
    body += finger + bd.Pos(0, 0, THICK / 2) * bd.Rot(180, 0, 0) * bd.Pos(0, 0, -THICK / 2) * finger
    assert len(body.solids()) == 1 and body.is_valid, "the key is not one sound solid"
    body.label, body.color = "Squeezer_key", "#2e9e83"
    return body


def seated(key, turn=0.0):
    """The key in the frame, turned this many degrees the way it winds."""
    return bd.Pos(0, 0, AXIS) * bd.Rot(turn, 0, 0) * bd.Pos(0, 0, -THICK / 2) * key


def shared(a, b):
    both = a & b
    return 0.0 if both is None else sum(s.volume for s in both.solids())


@step("out/Squeezer_frame.step")
def squeezer_frame():
    return frame_body()


@step("out/Squeezer_key.step")
def squeezer_key():
    return key_body()


@step("out/Squeezer_plate.step")
def squeezer_plate():
    """Both parts as they print, side by side."""
    key = bd.Pos(0, RIM + HANDLE_W / 2 + 8, 0) * key_body()
    key.label, key.color = "Squeezer_key", "#2e9e83"
    return bd.Compound(label="Squeezer_plate", children=[frame_body(), key])


@step("out/Squeezer_assembled.step")
def squeezer_assembled():
    """The key seated in the frame, to look at. Not for printing."""
    key = seated(key_body(), SEAT)
    key.label, key.color = "Squeezer_key", "#2e9e83"
    return bd.Compound(label="Squeezer_assembled", children=[frame_body(), key])


OVER = 1.6  # mm: the furthest a finger has to bend in a turn; the checks below prove it is enough
SEAT = 9.0  # degrees: the key sits in the teeth here touching nothing; the sweep below checks it

if __name__ == "__main__":
    frame, key = squeezer_frame().shape, squeezer_key().shape
    for name, part, want in (("frame", frame, [LENGTH, 2 * RIM, AXIS + RIM]),
                             ("key", key, [HANDLE + HANDLE_GAP + LENGTH + 1, HANDLE_W, THICK])):
        box = part.bounding_box()
        size = [round(v, 2) for v in (box.size.X, box.size.Y, box.size.Z)]
        print(f"{name}: {size} mm, {part.volume / 1000:.1f} cm3, stands on z {box.min.Z:.2f}")
        # within 0.2 mm: no tooth tip stands exactly at the top or the sides of the rim
        assert all(abs(a - b) <= 0.2 for a, b in zip(size, want)) and abs(box.min.Z) < 1e-6, (size, want)

    # The ratchet. First the key held rigid and turned through one tooth: seated, it shares nothing with the frame.
    pitch, steps = 360 / TEETH, 30
    sweep = [shared(frame, seated(key, n * pitch / steps)) for n in range(steps)]
    print("shared volume through one tooth, mm3:", " ".join(f"{v:.2f}" for v in sweep))
    free = [n * pitch / steps for n, v in enumerate(sweep) if v < 1e-3]
    assert free and free[0] <= SEAT <= free[-1], f"the key does not sit clear in the teeth at SEAT: clear at {free}"
    # Then the fingers bent outwards. Bent 0.5 mm they must clear the teeth when the key winds on (the slopes lift
    # them) and still be caught when it turns back (the upright faces hold them). Bent OVER they pass everything,
    # a whole turn round: the teeth, and the corners of the plate's foot, which stand 0.5 mm further out.
    half, whole = key_body(lift=0.5), key_body(lift=OVER)
    on, back = shared(frame, seated(half, free[-1] + 1.5)), shared(frame, seated(half, free[0] - 1.5))
    over = max(shared(frame, seated(whole, turn)) for turn in range(0, 360, 3))
    print(f"seated clear from {free[0]:.2f} to {free[-1]:.2f} degrees. Fingers bent 0.5 mm: wound on 1.5 degrees shares "
          f"{on:.3f} mm3, turned back 1.5 degrees shares {back:.3f} mm3. Bent {OVER} mm, a whole turn: at most {over:.3f} mm3")
    assert on < 1e-3, "the fingers do not ride up the teeth when the key winds on"
    assert back > 0.1, "the ratchet does not lock against turning back"
    assert over < 1e-3, "the fingers cannot pass over the teeth and the foot"
    strain = 1.5 * FINGER * OVER / (HANDLE_GAP + NEAR - 0.2) ** 2
    print(f"finger strain bent {OVER} mm: {strain:.1%}")
    assert strain < 0.02, "the fingers bend too far for PETG"
    walls = {"finger": FINGER, "shaft beside the window": (SHAFT - SLIT) / 2, "rim outside the hole": ROOT - BORE / 2}
    assert min(walls.values()) >= 1.2, walls
    squeezer_plate()
    squeezer_assembled()
