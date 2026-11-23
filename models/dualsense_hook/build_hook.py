# /// script
# dependencies = ["numpy"]
# ///
"""DualSense wall hook v1.4: the v1.3 shape as an exact solid, written with build123d and exported as STEP.

Prints with the back plate flat on the bed, no supports. Run it with text-to-cad's command, from this folder:
    cad server start
    cad run build_hook.py        (writes out/DualSenseWallHolder_v1.4.step and prints the checks)
    cad server stop
Change a dimension below and run it again. Give the STEP to slice_model.
"""
import math, numpy as np
import build123d as bd
from cad import step
T=5.0            # back plate thickness
PW, PH, PR, PRB = 36.0, 66.0, 16.0, 8.0   # plate half-width, height, top and bottom corner radii
A=34.0           # arm half-width (68 mm, fits the ~80 mm flat between the grips)
TH, RT, RB = 9.0, 6.0, 3.0      # arm thickness, top and bottom edge radii
YB=20.0          # cradle floor height at the wall, above plate bottom
SLOPE=math.tan(math.radians(10))
RF=6.0           # fillet where arm meets plate
XR=54.0          # where the front ramp starts
RE, S = 8.0, 0.75 # ramp ease radius and slope; with the 10 degree floor this totals 43 degrees, printable without supports
TAB_H, TAB_HW = 14.0, 17.0      # centre tongue height and half-width
LOW=4.0          # low lip height either side of the tongue (stays under the thumbsticks)
HOLD, NOSE = 3.0, 16.0
th=math.atan(S); ease_len=RE*math.sin(th); ease_rise=RE*(1-math.cos(th))
X_END = XR + ease_len + (TAB_H-ease_rise)/S + HOLD
def ramp(x):
    d=x-XR
    if d<=0: return 0.0
    if d<ease_len: return RE-math.sqrt(RE*RE-d*d)
    return min(TAB_H, ease_rise+(d-ease_len)*S)
def corner(z, half, r):
    e=abs(z)-(half-r)
    return 0.0 if e<=0 else r-math.sqrt(max(0.0,r*r-min(e,r)**2))
def smooth(v): v=min(1,max(0,v)); return v*v*(3-2*v)
def profile_cap(z):   # tallest the lip may be at this z
    bell = TAB_H*(1+math.cos(math.pi*z/TAB_HW))/2 if abs(z)<TAB_HW else 0.0
    low  = LOW*smooth((A-3-abs(z))/7)
    return max(bell, low)
def section(x, u):
    """Return (z, top, bottom) arrays for the cross-section at distance x from the wall."""
    if x<=T:
        d = max(0.4-x,0)*1.0 + max(x-(T-1.2),0)          # tiny foot chamfer at the bed, 1.2 mm chamfer at the front
        hw=PW-d; z=u*hw
        top=np.array([PH-d-corner(v,hw,PR-d) for v in z]); bot=np.array([d+corner(v,hw,PRB-d) for v in z])
        return z,top,bot
    fl=0.0
    if x-T<RF: fl=RF-math.sqrt(RF*RF-(RF-(x-T))**2)
    d=max(x-(X_END-1.0),0)                                # chamfer on the front face
    hw=A+min(fl,PW-1.4-A)-d*0.6; z=u*hw
    yf=YB+x*SLOPE
    keep=1.0                                              # fraction of arm thickness kept (tapers at the nose)
    if x>X_END-NOSE:
        q=(x-(X_END-NOSE))/NOSE; keep=1-0.7*(1-math.sqrt(max(0,1-q*q)))
    h=ramp(x)
    base=np.array([yf-corner(v,hw,RT) for v in z])                               # cradle surface
    thick=np.array([max(0.0,TH-corner(v,hw,RT)-corner(v,hw,RB)) for v in z])*keep
    lip=np.array([max(0.0,min(h,profile_cap(v))-d) for v in z])
    top=base+lip+fl
    bot=base-thick-fl
    return z,top,bot
N=61; u=-np.cos(np.linspace(0,math.pi,N))   # points across a section, closer together at the rounded ends
ARM_X=sorted(set(np.round(np.concatenate([[T+0.02],np.arange(T+0.25,T+RF,0.25),np.arange(T+RF,XR,2.0),
    np.arange(XR,X_END-1.0,0.25),np.arange(X_END-1.0,X_END+1e-9,0.2)]),4)))


def arm_section(x, at):
    """The arm's cross-section at distance x from the wall, as one closed curve placed at height `at`."""
    z,top,bot=section(x,u)
    ring=[(z[j],top[j],at) for j in range(N)]+[(z[j],bot[j],at) for j in range(N-1,-1,-1)]
    points=[ring[0]]
    for q in ring[1:]:
        if math.dist(q,points[-1])>1e-4 and math.dist(q,points[0])>1e-4: points.append(q)   # the two sides meet at a tip
    return bd.Face(bd.Wire([bd.Spline(*points,periodic=True)]))


def plate():
    """The back plate: a rounded outline, 16 mm corners at the top and 8 mm at the bottom, chamfered front and foot."""
    face=bd.Rectangle(2*PW,PH,align=(bd.Align.CENTER,bd.Align.MIN))
    face=bd.fillet(face.vertices().sort_by(bd.Axis.Y)[2:],PR)
    face=bd.fillet(face.vertices().sort_by(bd.Axis.Y)[:2],PRB)
    body=bd.extrude(face,T,dir=(0,0,1))
    body=bd.chamfer(body.edges().group_by(bd.Axis.Z)[-1],1.2)     # front edge, where the hand meets it
    return bd.chamfer(body.edges().group_by(bd.Axis.Z)[0],0.4)     # foot, against elephant's foot on the bed


@step("out/DualSenseWallHolder_v1.4.step")
def hook():
    # The arm starts half a millimetre inside the plate, so the two join as one solid.
    arm=bd.loft([arm_section(ARM_X[0],T-0.5)]+[arm_section(x,x) for x in ARM_X],ruled=True)
    body=plate()+arm
    assert len(body.solids())==1 and body.is_valid, "the plate and the arm did not join into one sound solid"
    body.label="DualSenseWallHolder_v1.4"
    return body


def meshed_volume(shape):
    """Volume in mm3, measured from a 0.02 mm mesh of the solid. The kernel's own `.volume` reads a third low
    on the arm's ruled spline faces (29.8 cm3 against 48.8 from its sections), so it is not used."""
    points,faces=shape.tessellate(0.02)
    v=np.array([(q.X,q.Y,q.Z) for q in points]); f=np.array(faces)
    return abs(float(np.einsum("ij,ij->i",v[f[:,0]],np.cross(v[f[:,1]],v[f[:,2]])).sum())/6)


if __name__=="__main__":
    made=hook()
    box=made.shape.bounding_box(); size=[round(v,2) for v in (box.size.X,box.size.Y,box.size.Z)]
    volume=meshed_volume(made.shape)
    print("width, height on wall, reach (mm)",size,"cm3",round(volume/1000,1),"solid g in PLA",round(volume/1000*1.24,1))
    # v1.3, the mesh this replaces: 72 x 66 x 78.33 mm, 71.76 cm3.
    assert all(abs(a-b)<0.5 for a,b in zip(size,(72.0,66.0,78.33))), size
    assert abs(volume/71756.8-1)<0.03, volume
    # clearance to thumbsticks (cap centre 25.5 mm off centre, 16.7 mm above the body edge, radius 9.75; lowest point 6.95)
    print("lip height under stick zone |z|=16..36:",[round(profile_cap(z),1) for z in (16,20,25.5,30,34)])
    for h in (7,10,13):
        hw=TAB_HW/math.pi*math.acos(2*h/TAB_H-1); c=25.5-math.sqrt(9.75**2-(h-16.7)**2); print(f"tongue half-width at {h} mm: {hw:.1f}; stick inner edge {c:.1f}; clearance {c-hw:.1f}")
    g=9.81*0.28; print("arm root stress MPa",round(6*g*0.045/((2*A/1000)*(TH/1000)**2)/1e6,2))
