"""A wall plug (rawlplug) for a 7 mm hole, 30 mm long, for 4 to 5 mm screws: the size of a standard brown plug.

Prints standing on its collar, nose up, so each layer is a ring around the screw and the plug resists
splitting as the screw spreads it. No supports. Print it in PETG or nylon; PLA is brittle and can crack.
Run it with text-to-cad's command, from this folder:
    cad server start
    cad run build_rawlplug.py        (writes out/Rawlplug_7x30.step and prints the checks)
    cad server stop
Change a dimension below and run it again. Give the STEP to slice_model.
"""
import build123d as bd
from cad import step

HOLE = 7.0            # the drill size the plug is for; the body is this wide
LENGTH = 30.0         # overall, collar to nose
COLLAR_D, COLLAR = 9.0, 0.8     # a rim that stops the plug going in too far, and its footing on the bed
NOSE, NOSE_D = 4.0, 5.0         # the last 4 mm taper to 5 mm, to start it in the hole
BORE_MOUTH, BORE_END = 3.4, 1.8 # the screw's hole: a 4 to 5 mm screw bites all the way, and bites harder towards the nose
BORE_STOP = 2.0       # solid plastic left at the nose
SLOT, SLOT_LENGTH = 1.0, 22.0   # the split that lets the two halves spread, measured back from the nose
RIB, RIB_WIDTH = 0.4, 0.8       # four ribs standing this proud of the body, to bite the wall and stop the plug turning
RIB_LENGTH = 16.0     # ribs run from the collar this far up the body


@step("out/Rawlplug_7x30.step")
def rawlplug():
    low = (bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    plug = bd.Cylinder(HOLE / 2, LENGTH - NOSE, align=low)
    plug += bd.Pos(0, 0, LENGTH - NOSE) * bd.Cone(HOLE / 2, NOSE_D / 2, NOSE, align=low)
    plug += bd.Cylinder(COLLAR_D / 2, COLLAR, align=low)
    for angle in (45, 135, 225, 315):       # between the split's two faces, so no rib is cut in half
        plug += bd.Rot(0, 0, angle) * bd.Pos(HOLE / 2, 0, 0) * bd.Box(2 * RIB, RIB_WIDTH, RIB_LENGTH, align=low)
    plug -= bd.Cone(BORE_MOUTH / 2, BORE_END / 2, LENGTH - BORE_STOP, align=low)
    plug -= bd.Pos(0, 0, LENGTH - SLOT_LENGTH) * bd.Box(SLOT, HOLE + 2, SLOT_LENGTH + 1, align=low)
    assert len(plug.solids()) == 1 and plug.is_valid, "the plug is not one sound solid"
    plug.label = "Rawlplug_7x30"
    return plug


if __name__ == "__main__":
    made = rawlplug()
    box = made.shape.bounding_box()
    size = [round(v, 2) for v in (box.size.X, box.size.Y, box.size.Z)]
    print("across the collar, across the collar, length (mm)", size, "volume mm3", round(made.shape.volume, 1))
    assert size == [COLLAR_D, COLLAR_D, LENGTH], size
    wall_mouth, wall_nose = (HOLE - BORE_MOUTH) / 2, (NOSE_D - BORE_END) / 2
    print(f"wall at the mouth {wall_mouth:.2f} mm, at the nose {wall_nose:.2f} mm; across the ribs {HOLE + 2 * RIB:.1f} mm")
    assert min(wall_mouth, wall_nose) >= 1.2, "a wall under three lines of a 0.4 mm nozzle is too thin for a plug"
