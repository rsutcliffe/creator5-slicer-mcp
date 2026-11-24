"""Wall plugs (rawlplugs) for 4 to 5 mm screws: one for a 7 mm hole, the size of a standard brown plug, and one
for a 9 mm hole, both 30 mm long; and a 40 mm plug for an 8 mm hole in hard brick, shaped for grip.

Prints standing on its collar, nose up, so each layer is a ring around the screw and the plug resists
splitting as the screw spreads it. No supports. Print it in PETG or nylon; PLA is brittle and can crack.
Run it with text-to-cad's command, from this folder:
    cad server start
    cad run build_rawlplug.py        (writes the three STEP files into out/ and prints the checks)
    cad server stop
Change a dimension below and run it again. Give the STEP to slice_model.
"""
import build123d as bd
from cad import step

LENGTH = 30.0         # overall, collar to nose
COLLAR_OVER, COLLAR = 2.0, 0.8  # a rim this much wider than the body, which stops the plug going in too far and is its footing on the bed
NOSE, NOSE_UNDER = 4.0, 2.0     # the last 4 mm taper to 2 mm under the body's width, to start it in the hole
BORE_MOUTH, BORE_END = 3.4, 1.8 # the screw's hole: a 4 to 5 mm screw bites all the way, and bites harder towards the nose
BORE_STOP = 2.0       # solid plastic left at the nose
SLOT, SLOT_LENGTH = 1.0, 22.0   # the split that lets the two halves spread, measured back from the nose
RIB, RIB_WIDTH = 0.4, 0.8       # four ribs standing this proud of the body, to bite the wall and stop the plug turning
RIB_LENGTH = 16.0     # ribs run from the collar this far up the body

# The grip plug, for hard brick. Hard brick does not crush, so a plug cannot bite into it: the hold comes from
# spreading hard against the hole, deep in the wall, over a long length. The shape follows bought plugs for solid
# walls; no pull-out figure exists for a printed one, so test it by hand before trusting it.
GRIP_LENGTH = 40.0    # the length bought 8 mm plugs are; a third more hole to hold than the 30 mm plugs
NECK = 10.0           # smooth and unsplit from the collar, so nothing spreads at the face of the wall, where old brick spalls
TOOTH, TOOTH_UNDER = 0.2, 0.3   # saw-tooth rings: tips this proud of the hole's width, valleys this far under it
TOOTH_PITCH, TOOTH_RISE = 2.6, 0.5  # each tooth flares out at 45 degrees (prints with no supports), then slopes back to the nose side


def plug(hole, length=LENGTH, grip=False):
    low = (bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    shank = length - NOSE                           # where the nose taper starts
    smooth = NECK if grip else shank                # the plain cylinder, from the collar
    waist = hole / 2 - (TOOTH_UNDER if grip else 0)
    body = bd.Cylinder(hole / 2, smooth, align=low)
    if grip:    # the sharp side of each tooth faces the collar, so the plug goes in easily and resists coming out
        for n in range(round((shank - NECK) / TOOTH_PITCH)):
            z = NECK + n * TOOTH_PITCH
            body += bd.Pos(0, 0, z) * bd.Cone(waist, hole / 2 + TOOTH, TOOTH_RISE, align=low)
            body += bd.Pos(0, 0, z + TOOTH_RISE) * bd.Cone(hole / 2 + TOOTH, waist, TOOTH_PITCH - TOOTH_RISE, align=low)
    body += bd.Pos(0, 0, shank) * bd.Cone(waist, (hole - NOSE_UNDER) / 2, NOSE, align=low)
    body += bd.Cylinder((hole + COLLAR_OVER) / 2, COLLAR, align=low)
    for angle in (45, 135, 225, 315):       # between the split's faces, so no rib is cut in half
        body += bd.Rot(0, 0, angle) * bd.Pos(hole / 2, 0, 0) * bd.Box(2 * RIB, RIB_WIDTH, min(RIB_LENGTH, smooth), align=low)
    body -= bd.Cone(BORE_MOUTH / 2, BORE_END / 2, length - BORE_STOP, align=low)
    slot_length = length - NECK if grip else SLOT_LENGTH
    for angle in ((0, 90) if grip else (0,)):   # the grip plug splits four ways, to press on the hole all round
        body -= bd.Rot(0, 0, angle) * bd.Pos(0, 0, length - slot_length) * bd.Box(SLOT, hole + 2, slot_length + 1, align=low)
    assert len(body.solids()) == 1 and body.is_valid, "the plug is not one sound solid"
    body.label = f"Rawlplug_{hole:g}x{length:g}" + ("_grip" if grip else "")
    return body


@step("out/Rawlplug_7x30.step")
def rawlplug_7x30():
    return plug(7.0)


@step("out/Rawlplug_9x30.step")
def rawlplug_9x30():
    return plug(9.0)


@step("out/Rawlplug_8x40_grip.step")
def rawlplug_8x40_grip():
    return plug(8.0, GRIP_LENGTH, grip=True)


if __name__ == "__main__":
    for hole, length, grip, made in ((7.0, LENGTH, False, rawlplug_7x30()), (9.0, LENGTH, False, rawlplug_9x30()),
                                     (8.0, GRIP_LENGTH, True, rawlplug_8x40_grip())):
        box = made.shape.bounding_box()
        size = [round(v, 2) for v in (box.size.X, box.size.Y, box.size.Z)]
        walls = {"at the mouth": (hole - BORE_MOUTH) / 2, "at the nose": (hole - NOSE_UNDER - BORE_END) / 2}
        if grip:    # the thinnest place: the first valley, where the screw's hole is still near its widest
            bore_there = BORE_MOUTH - (BORE_MOUTH - BORE_END) * NECK / (length - BORE_STOP)
            walls["under the first tooth"] = (hole - 2 * TOOTH_UNDER - bore_there) / 2
        widest = f"across the teeth {hole + 2 * TOOTH:.1f} mm" if grip else f"across the ribs {hole + 2 * RIB:.1f} mm"
        print(f"{hole:g} mm plug: across the collar and length (mm) {size}, volume mm3 {made.shape.volume:.1f}, wall "
              + ", ".join(f"{where} {wall:.2f} mm" for where, wall in walls.items()) + f", {widest}")
        assert size == [hole + COLLAR_OVER, hole + COLLAR_OVER, length], size
        assert min(walls.values()) >= 1.2, "a wall under three lines of a 0.4 mm nozzle is too thin for a plug"
