"""Wall plugs (rawlplugs), 30 mm long, for 4 to 5 mm screws: one for a 7 mm hole, the size of a standard brown
plug, and one for a 9 mm hole.

Prints standing on its collar, nose up, so each layer is a ring around the screw and the plug resists
splitting as the screw spreads it. No supports. Print it in PETG or nylon; PLA is brittle and can crack.
Run it with text-to-cad's command, from this folder:
    cad server start
    cad run build_rawlplug.py        (writes out/Rawlplug_7x30.step and out/Rawlplug_9x30.step, and prints the checks)
    cad server stop
Change a dimension below and run it again. Give the STEP to slice_model.
"""
import build123d as bd
from cad import step

HOLES = (7.0, 9.0)    # the drill sizes plugs are made for; each body is as wide as its hole
LENGTH = 30.0         # overall, collar to nose
COLLAR_OVER, COLLAR = 2.0, 0.8  # a rim this much wider than the body, which stops the plug going in too far and is its footing on the bed
NOSE, NOSE_UNDER = 4.0, 2.0     # the last 4 mm taper to 2 mm under the body's width, to start it in the hole
BORE_MOUTH, BORE_END = 3.4, 1.8 # the screw's hole: a 4 to 5 mm screw bites all the way, and bites harder towards the nose
BORE_STOP = 2.0       # solid plastic left at the nose
SLOT, SLOT_LENGTH = 1.0, 22.0   # the split that lets the two halves spread, measured back from the nose
RIB, RIB_WIDTH = 0.4, 0.8       # four ribs standing this proud of the body, to bite the wall and stop the plug turning
RIB_LENGTH = 16.0     # ribs run from the collar this far up the body


def plug(hole):
    low = (bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    body = bd.Cylinder(hole / 2, LENGTH - NOSE, align=low)
    body += bd.Pos(0, 0, LENGTH - NOSE) * bd.Cone(hole / 2, (hole - NOSE_UNDER) / 2, NOSE, align=low)
    body += bd.Cylinder((hole + COLLAR_OVER) / 2, COLLAR, align=low)
    for angle in (45, 135, 225, 315):       # between the split's two faces, so no rib is cut in half
        body += bd.Rot(0, 0, angle) * bd.Pos(hole / 2, 0, 0) * bd.Box(2 * RIB, RIB_WIDTH, RIB_LENGTH, align=low)
    body -= bd.Cone(BORE_MOUTH / 2, BORE_END / 2, LENGTH - BORE_STOP, align=low)
    body -= bd.Pos(0, 0, LENGTH - SLOT_LENGTH) * bd.Box(SLOT, hole + 2, SLOT_LENGTH + 1, align=low)
    assert len(body.solids()) == 1 and body.is_valid, "the plug is not one sound solid"
    body.label = f"Rawlplug_{hole:g}x{LENGTH:g}"
    return body


@step("out/Rawlplug_7x30.step")
def rawlplug_7x30():
    return plug(7.0)


@step("out/Rawlplug_9x30.step")
def rawlplug_9x30():
    return plug(9.0)


if __name__ == "__main__":
    for hole, made in zip(HOLES, (rawlplug_7x30(), rawlplug_9x30())):
        box = made.shape.bounding_box()
        size = [round(v, 2) for v in (box.size.X, box.size.Y, box.size.Z)]
        wall_mouth, wall_nose = (hole - BORE_MOUTH) / 2, (hole - NOSE_UNDER - BORE_END) / 2
        print(f"{hole:g} mm plug: across the collar and length (mm) {size}, volume mm3 {made.shape.volume:.1f}, "
              f"wall at the mouth {wall_mouth:.2f} mm, at the nose {wall_nose:.2f} mm, across the ribs {hole + 2 * RIB:.1f} mm")
        assert size == [hole + COLLAR_OVER, hole + COLLAR_OVER, LENGTH], size
        assert min(wall_mouth, wall_nose) >= 1.2, "a wall under three lines of a 0.4 mm nozzle is too thin for a plug"
