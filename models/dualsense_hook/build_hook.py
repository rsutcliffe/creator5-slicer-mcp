"""DualSense wall hook v1.3: sculpted, lofted from cross-sections. Prints with the back plate flat on the bed, no supports."""
import math, numpy as np, trimesh
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
N=121; u=-np.cos(np.linspace(0,math.pi,N))
xs=sorted(set(np.round(np.concatenate([[0,0.2,0.4],np.arange(T-1.2,T+1e-9,0.2),[T+0.02],np.arange(T+0.25,T+RF,0.25),
    np.arange(T+RF,XR,2.0),np.arange(XR,X_END-1.0,0.25),np.arange(X_END-1.0,X_END+1e-9,0.2)]),4)))
V=[];rings=[]
for x in xs:
    z,top,bot=section(x,u); start=len(V)
    for j in range(N): V.append((z[j],top[j],x))
    for j in range(N-1,-1,-1): V.append((z[j],bot[j],x))
    rings.append(start)
M=2*N; F=[]
for a,b in zip(rings[:-1],rings[1:]):
    for k in range(M):
        k2=(k+1)%M; F+= [(a+k,b+k,b+k2),(a+k,b+k2,a+k2)]
def cap(s,flip):
    out=[]
    for j in range(N-1):
        t0,t1=s+j,s+j+1; b0,b1=s+M-1-j,s+M-2-j
        out+= [(t0,t1,b1),(t0,b1,b0)]
    return [f[::-1] for f in out] if flip else out
F+=cap(rings[0],False)+cap(rings[-1],True)
m=trimesh.Trimesh(np.array(V),np.array(F),process=True)
m.update_faces(m.nondegenerate_faces()); m.remove_unreferenced_vertices(); trimesh.repair.fix_normals(m)
print("watertight",m.is_watertight,"volume",m.is_volume,"faces",len(m.faces))
m.apply_translation([0,-m.bounds[0][1],0])
name="20261115_Personal_DualSenseWallHolder_v1.3"
import os; os.makedirs("out",exist_ok=True); m.export(f"out/{name}.stl")
print("print-orientation bounds (width, height-on-wall, reach)",m.bounds.round(1).tolist(),"cm3",round(m.volume/1000,1),"solid g",round(m.volume/1000*1.24,1),"reach",round(X_END,1))
import sys; sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","..","src"))
from creator5_mcp import model; from pathlib import Path
a=model.analyse(Path(f"out/{name}.stl")); print({k:a[k] for k in ("size_mm","bed_contact_area_mm2","overhang_area_mm2","overhang_fraction","height_to_footprint")})
n=m.face_normals; ar=m.area_faces; zc=m.triangles[:,:,2].min(axis=1); bad=(n[:,2]<-0.7072)&(zc>0.3)
print("faces steeper than 45 deg overhang: area mm2",round(float(ar[bad].sum()),1))
# clearance to thumbsticks (cap centre 25.5 mm off centre, 16.7 mm above the body edge, radius 9.75; lowest point 6.95)
print("lip height under stick zone |z|=16..36:",[round(profile_cap(z),1) for z in (16,20,25.5,30,34)])
for h in (7,10,13):
    hw=TAB_HW/math.pi*math.acos(2*h/TAB_H-1); c=25.5-math.sqrt(9.75**2-(h-16.7)**2); print(f"tongue half-width at {h} mm: {hw:.1f}; stick inner edge {c:.1f}; clearance {c-hw:.1f}")
g=9.81*0.28; print("arm root stress MPa",round(6*g*0.045/((2*A/1000)*(TH/1000)**2)/1e6,2))
