#!/usr/bin/env python3
"""Write a small two-colour test piece: two 15 x 30 x 4 mm blocks side by side, one per toolhead.

Both colours print on every layer, so a 0.2 mm slice changes toolhead about twenty times. It proves
toolhead changes and the purge tower on the machine in under an hour, before a long multi-colour job.

    python models/two_colour_test/build_test.py OUTPUT.3mf
"""

import sys
import zipfile

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def box(x0: float, sx: float, sy: float, sz: float) -> tuple[list, list]:
    v = [(x0 + x, y, z) for z in (0, sz) for y in (0, sy) for x in (0, sx)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    return v, [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]


def mesh(oid: int, name: str, pindex: int, x0: float) -> str:
    v, t = box(x0, 15, 30, 4)
    vertices = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in v)
    triangles = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in t)
    return (f'<object id="{oid}" type="model" name="{name}" pid="1" pindex="{pindex}">'
            f'<mesh><vertices>{vertices}</vertices><triangles>{triangles}</triangles></mesh></object>')


def main(out: str) -> None:
    model = (f'<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xmlns="{CORE}"><resources>'
             '<basematerials id="1"><base name="Toolhead 1" displaycolor="#161616FF"/>'
             '<base name="Toolhead 2" displaycolor="#2F54E0FF"/></basematerials>'
             + mesh(2, "Toolhead 1 half", 0, 0) + mesh(3, "Toolhead 2 half", 1, 15)
             + '<object id="4" type="model" name="Two colour test"><components><component objectid="2"/>'
             '<component objectid="3"/></components></object></resources><build><item objectid="4"/></build></model>')
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", model)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "two_colour_test.3mf")
