
"""Red betta in deep teal water, thick-paint study, 1200x900.
Deterministic plan builder for the svg-rebuild-and-paint engine.
"""
from __future__ import annotations
import math, sys
from pathlib import Path
import numpy as np
import cv2

SKILL = Path(r"D:\deepseek-harness-dsh-v0.2.2rc1\.dsh-home\skills\svg-rebuild-and-paint")
sys.path.insert(0, str(SKILL / "scripts"))
import plan_io
import shape_ops

OUT = Path(r"C:\Users\chesand\AppData\Local\Temp\cold-v15\out")
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1200, 900
rng = np.random.default_rng(20260615)

doc = {"canvas": [W, H], "layers": [], "strokes": []}
LAYER_ORDER = [
    "under", "water", "water_deep", "rays", "caustic", "far_flora", "rocks",
    "kelp", "fish_aura", "dorsal", "anal", "tail", "tail_ray", "tail_edge",
    "pelvic", "body", "body_form", "body_shadow", "body_light", "scales",
    "head", "pectoral", "fin_glints", "fish_light", "contour", "bubbles", "particles",
    "glints", "vignette"
]
BLENDS = {
    "under":"normal", "water":"normal", "water_deep":"normal", "rays":"screen",
    "caustic":"screen", "far_flora":"normal", "rocks":"normal", "kelp":"normal",
    "fish_aura":"normal", "dorsal":"normal", "anal":"normal", "tail":"normal",
    "tail_ray":"normal", "tail_edge":"normal", "pelvic":"normal", "body":"normal",
    "body_form":"normal", "body_shadow":"normal", "body_light":"normal",
    "scales":"normal", "head":"normal", "pectoral":"normal", "fin_glints":"screen",
    "fish_light":"normal", "contour":"normal", "bubbles":"screen", "particles":"screen",
    "glints":"screen", "vignette":"softlight"
}

def c8(c):
    return [int(np.clip(round(float(v)), 0, 255)) for v in c]

def mix(a, b, t):
    return np.asarray(a, np.float32) * (1.0-t) + np.asarray(b, np.float32) * t

def jc(c, amt=4, cool=0.0):
    v = np.asarray(c, np.float32) + rng.uniform(-amt, amt, 3)
    v += np.array([-0.3*cool, 0.25*cool, 0.55*cool], np.float32)
    return c8(v)

def add(layer, pts, size, color, opacity=1.0, preset="round_hard", hardness=0.72, pressure=1.0, tag=None):
    ps = []
    for p in pts:
        x, y = float(p[0]), float(p[1])
        pr = float(p[2]) if len(p) > 2 else pressure
        ps.append([round(x,2), round(y,2), round(float(np.clip(pr,0,1)),3)])
    s = {"layer":layer, "preset":preset, "size":round(float(size),2),
         "opacity":round(float(np.clip(opacity,0,1)),3), "color":c8(color),
         "hardness":round(float(np.clip(hardness,0,1)),3), "points":ps}
    if tag: s["tag"] = tag
    doc["strokes"].append(s)

def poly(layer, pts, color, color2=None, size=18, spacing=None, opacity=1.0,
         preset="round_hard", hardness=0.72, jitter=0.28, edge=True,
         gradient_angle=0.0, seed=0, holes=None, pressure=1.0, clip=False):
    shape_ops.fill_polygon(doc, layer, pts, color=c8(color),
        color2=(c8(color2) if color2 is not None else None),
        size=float(size), spacing=(float(spacing) if spacing else None),
        opacity=float(opacity), preset=preset, jitter=float(jitter),
        hardness=float(hardness), edge=bool(edge), gradient_angle=float(gradient_angle),
        seed=int(seed), holes=holes, pressure=float(pressure), clip=bool(clip))

def tapered(layer, d, color, size, size_end, steps=20, opacity=1.0,
            preset="round_hard", hardness=0.7, copies=1, spread=0.0,
            pressure=1.0, pressure_end=None):
    shape_ops.tapered_stroke(doc, layer, d, color=c8(color), size=float(size),
        size_end=float(size_end), steps=int(steps), opacity=float(opacity),
        preset=preset, hardness=float(hardness), copies=int(copies),
        spread=float(spread), pressure=float(pressure),
        pressure_end=(float(pressure_end) if pressure_end is not None else None))

def line_pts(x0,y0,x1,y1,n=3,jit=0.0):
    out=[]
    for i in range(n):
        t=i/(n-1)
        out.append((x0+(x1-x0)*t + rng.uniform(-jit,jit),
                    y0+(y1-y0)*t + rng.uniform(-jit,jit)))
    return out

def curved(x,y,length,angle,bend=0.0,seg=4):
    dx,dy=math.cos(angle)*length,math.sin(angle)*length
    nx,ny=-math.sin(angle),math.cos(angle)
    out=[]
    for i in range(seg+1):
        t=i/seg
        out.append((x-dx*0.5+dx*t + nx*bend*math.sin(math.pi*t),
                    y-dy*0.5+dy*t + ny*bend*math.sin(math.pi*t)))
    return out

def bez(p0,p1,p2,p3,n=32):
    t=np.linspace(0,1,n)[:,None]
    p=np.asarray([p0,p1,p2,p3],np.float32)
    return ((1-t)**3*p[0] + 3*(1-t)**2*t*p[1] + 3*(1-t)*t*t*p[2] + t**3*p[3]).tolist()

def ellipse_pts(cx,cy,rx,ry,n=36,ang=0.0):
    a=np.linspace(0,math.tau,n,endpoint=False)
    ca,sa=math.cos(math.radians(ang)),math.sin(math.radians(ang))
    xx,yy=rx*np.cos(a),ry*np.sin(a)
    return [(cx+xx[i]*ca-yy[i]*sa, cy+xx[i]*sa+yy[i]*ca) for i in range(n)]

def mask_polys(polys, blur=0.0):
    m=np.zeros((H,W),np.uint8)
    for p in polys:
        cv2.fillPoly(m,[np.round(np.asarray(p,np.float32)).astype(np.int32)],255)
    if blur>0:
        m=cv2.GaussianBlur(m.astype(np.float32),(0,0),blur)
    return m

def shape_sample(mask,n=1):
    ys,xs=np.where(mask>0)
    if len(xs)==0: return np.zeros((0,2))
    ids=rng.integers(0,len(xs),n)
    return np.stack([xs[ids],ys[ids]],axis=1).astype(np.float32)

def contour_points(mask, step=7):
    cs,_=cv2.findContours((mask>0).astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)
    out=[]
    for c in cs:
        if len(c)<12: continue
        for i in range(0,len(c),step):
            j=(i+step)%len(c)
            p=c[i,0].astype(np.float32); q=c[j,0].astype(np.float32)
            if np.linalg.norm(q-p)<1.0: continue
            out.append((p,q))
    return out

def edge_rim(layer,mask,inside_color,rim_color,lit_test=None,step=6,size_min=2.0,size_max=6.0,op=0.55):
    for p,q in contour_points(mask,step):
        x,y=p; ang=math.atan2(q[1]-p[1],q[0]-p[0])
        if lit_test is not None and not lit_test(x,y): continue
        if rng.random()<0.33: continue
        ln=rng.uniform(8,25)
        add(layer,curved(float(x),float(y),ln,ang+rng.uniform(-0.18,0.18),rng.uniform(-1.5,1.5),3),
            rng.uniform(size_min,size_max),jc(rim_color,7),rng.uniform(op*0.65,op*1.15),
            rng.choice(["dry","bristle","round_hard"]),rng.uniform(0.5,0.9))

# ---------------------------------------------------------------- water field
yy,xx=np.mgrid[0:H,0:W].astype(np.float32)
n_small=cv2.resize(rng.random((38,50)).astype(np.float32),(W,H),interpolation=cv2.INTER_CUBIC)
n_mid=cv2.resize(rng.random((95,140)).astype(np.float32),(W,H),interpolation=cv2.INTER_CUBIC)
n_fine=cv2.resize(rng.random((260,340)).astype(np.float32),(W,H),interpolation=cv2.INTER_CUBIC)
noise=(0.45*n_small+0.33*n_mid+0.22*n_fine)
water=np.empty((H,W,3),np.float32)
stops=[0.0,0.22,0.46,0.70,1.0]
cols=np.array([[16,63,70],[11,51,61],[7,37,48],[4,26,36],[2,15,23]],np.float32)
for c in range(3):
    water[...,c]=np.interp(yy/H,stops,cols[:,c])
# broad top-left undertow light and a soft aqua veil in the mid water
glow=np.exp(-(((xx-245)/470)**2 + ((yy+55)/600)**2))
water += glow[...,None]*np.array([27,60,50],np.float32)
water += np.exp(-(((xx-760)/520)**2 + ((yy-350)/520)**2))[...,None]*np.array([-3,9,10],np.float32)
water += (noise[...,None]-0.5)*np.array([10,14,15],np.float32)
water += np.exp(-(((xx-80)/250)**2 + ((yy-820)/220)**2))[...,None]*np.array([-2,2,5],np.float32)
water=np.clip(water,0,255)

def water_col(x,y):
    xi=int(np.clip(x,0,W-1)); yi=int(np.clip(y,0,H-1))
    return water[yi,xi]

# ---------------------------------------------------------------- underpainting
poly("under",[(0,0),(W,0),(W,H),(0,H)],(12,52,61),(2,16,24),size=112,spacing=20,
     opacity=1.0,preset="sponge",hardness=0.55,jitter=0.62,edge=False,gradient_angle=90,seed=3)

# broad moving-water passages
for y in np.arange(8,H+18,19):
    x=-60
    while x<W+80:
        px=x+rng.uniform(-34,34); py=y+rng.uniform(-12,12)
        c=water_col(px,py)*rng.uniform(0.84,1.08) + rng.uniform(-5,5,3)
        ang=rng.uniform(-0.08,0.10)+0.045*math.sin(px/160+py/110)
        ln=rng.uniform(80,240)
        add("water",curved(px,py,ln,ang,rng.uniform(-8,8),5),rng.uniform(22,63),jc(c,4),
            rng.uniform(0.32,0.72),rng.choice(["sponge","bristle","dry","round_soft"]),
            rng.uniform(0.35,0.78),rng.uniform(0.72,1.0))
        x+=rng.uniform(45,95)

# deep murk and bottom counter-currents
for i in range(520):
    py=rng.uniform(470,940); px=rng.uniform(-80,W+80)
    c=water_col(px,py)*rng.uniform(0.66,0.96)
    ang=rng.uniform(-0.16,0.05)
    add("water_deep",curved(px,py,rng.uniform(90,260),ang,rng.uniform(-8,8),5),
        rng.uniform(20,58),jc(c,3),rng.uniform(0.16,0.42),
        rng.choice(["sponge","dry","bristle"]),rng.uniform(0.3,0.7),rng.uniform(0.65,1.0))

# thin dragged current filaments
for i in range(180):
    py=rng.uniform(25,865); px=rng.uniform(-90,W+60)
    c=water_col(px,py)+rng.uniform(8,25,3)
    ang=rng.uniform(-0.05,0.12)+0.08*math.sin(py/140)
    add("water",curved(px,py,rng.uniform(140,420),ang,rng.uniform(-12,12),6),
        rng.uniform(1.5,5.5),jc(c,5),rng.uniform(0.035,0.13),
        rng.choice(["dry","chalk","liner"]),rng.uniform(0.2,0.7),rng.uniform(0.6,1.0))

# ---------------------------------------------------------------- light shafts
# long airbrushed shafts from high left
shafts=[(-90,0,430,900,94,0.030),(-55,0,210,900,60,0.024),(105,0,700,900,130,0.020),
        (360,0,590,900,72,0.018),(575,0,1010,900,95,0.013)]
for sx,sy,ex,ey,sw,op in shafts:
    for k in range(14):
        t0=rng.uniform(0.0,0.28); t1=rng.uniform(0.52,1.04)
        x0=sx+(ex-sx)*t0+rng.uniform(-40,40); y0=sy+(ey-sy)*t0
        x1=sx+(ex-sx)*t1+rng.uniform(-65,65); y1=sy+(ey-sy)*t1
        add("rays",[(x0,y0),(x0*0.45+x1*0.55,y0*0.45+y1*0.55),(x1,y1)],
            sw*rng.uniform(0.7,1.5),(56,153,140),op*rng.uniform(0.45,1.15),
            rng.choice(["airbrush","airbrush","round_soft"]),0.16,1.0)
# a few sharper caustic ribbons near the upper water
for i in range(80):
    px=rng.uniform(-50,900); py=rng.uniform(20,360)
    ln=rng.uniform(60,250)
    ang=0.55+rng.uniform(-0.10,0.10)
    add("caustic",curved(px,py,ln,ang,rng.uniform(-16,16),5),
        rng.uniform(2,8),(91,185,158),rng.uniform(0.025,0.10),
        rng.choice(["dry","chalk","liner"]),rng.uniform(0.2,0.7),rng.uniform(0.65,1.0))
# warm-cool suspended light bloom near upper left
poly("rays",ellipse_pts(170,65,260,170,40),(45,121,113),None,size=90,spacing=27,
     opacity=0.030,preset="airbrush",hardness=0.12,jitter=0.3,edge=False,seed=8)

# ---------------------------------------------------------------- bottom structure
# far rocks and silt shelves
rock_l=[(-60,792),(55,742),(145,748),(235,784),(342,806),(385,900),(-60,900)]
rock_r=[(860,845),(938,790),(1024,772),(1112,800),(1207,768),(1240,900),(840,900)]
rock_c=[(350,900),(420,832),(520,824),(600,851),(665,900)]
for i,pol in enumerate([rock_l,rock_r,rock_c]):
    poly("rocks",pol,(3,27,34),(10,46,44),size=22,spacing=8,opacity=0.92,
         preset="sponge",hardness=0.62,jitter=0.45,edge=True,gradient_angle=90,seed=40+i)
# rock ridges
for i in range(150):
    px=rng.uniform(0,W); py=rng.uniform(790,900)
    if not (py>810 or rng.random()<0.4): continue
    add("rocks",curved(px,py,rng.uniform(18,90),rng.uniform(-0.18,0.18),rng.uniform(-5,5),3),
        rng.uniform(2,9),jc(mix((8,47,43),(25,68,54),rng.random()),5),rng.uniform(0.08,0.34),
        rng.choice(["dry","bristle","chalk"]),rng.uniform(0.35,0.8),rng.uniform(0.7,1.0))

# distant kelp silhouettes
def kelp_poly(basex,topx,bottomy,topy,width):
    pts=bez((basex,bottomy),(basex-35,bottomy-120),(topx+55,bottomy-280),(topx,topy),36)
    left=[]; right=[]
    for i,(x,y) in enumerate(pts):
        if i==0: dx,dy=pts[1][0]-x,pts[1][1]-y
        elif i==len(pts)-1: dx,dy=x-pts[i-1][0],y-pts[i-1][1]
        else: dx,dy=pts[i+1][0]-pts[i-1][0],pts[i+1][1]-pts[i-1][1]
        l=math.hypot(dx,dy)+1e-6; nx,ny=-dy/l,dx/l
        w=width*(0.35+0.65*math.sin(math.pi*(i/(len(pts)-1))**0.75))
        w*=(0.75+0.25*math.sin(i*0.9))
        left.append((x+nx*w*0.5,y+ny*w*0.5)); right.append((x-nx*w*0.5,y-ny*w*0.5))
    return left+right[::-1]
kelps=[(55,925,12,500,48),(112,910,62,405,36),(182,930,128,610,28),
       (1050,920,1090,465,42),(1112,925,1165,370,34),(985,930,942,565,30)]
for i,(bx,by,tx,ty,wd) in enumerate(kelps):
    pol=kelp_poly(bx,tx,by,ty,wd)
    poly("kelp",pol,(8,55,50),(20,76,59),size=13,spacing=5.2,opacity=0.88,
         preset="dry",hardness=0.55,jitter=0.25,edge=True,gradient_angle=90,seed=70+i)
    # central vein and lit edge
    pts=bez((bx,by),(bx-35,by-120),(tx+55,by-280),(tx,ty),30)
    for j in range(0,len(pts)-4,4):
        x,y=pts[j]; x2,y2=pts[j+4]
        add("kelp",[(x,y),(x2,y2)],rng.uniform(1.2,3.2),(29,99,75),rng.uniform(0.18,0.45),
            rng.choice(["liner","dry"]),0.65,1.0)
    for j in range(8,len(pts)-8,6):
        x,y=pts[j]
        if rng.random()<0.55:
            add("kelp",curved(x-2,y,rng.uniform(8,25),rng.uniform(-0.9,0.2),2,3),
                rng.uniform(1.2,4),(71,129,88),rng.uniform(0.10,0.35),
                rng.choice(["dry","chalk"]),0.45,1.0)

# little bottom plants / silt strokes
for i in range(380):
    px=rng.uniform(0,W); py=rng.uniform(760,905)
    ln=rng.uniform(10,65)
    add("far_flora",[(px,py),(px+rng.uniform(-10,10),py-ln*0.55),(px+rng.uniform(-22,22),py-ln)],
        rng.uniform(1.3,5.3),jc(mix((7,52,46),(35,94,73),rng.random()),6),rng.uniform(0.15,0.56),
        rng.choice(["dry","chalk","bristle","liner"]),rng.uniform(0.25,0.7),rng.uniform(0.6,1.0))

# ---------------------------------------------------------------- fish geometry and fields
# body: a compact, forward-leaning teardrop with a deep chest
body_pts=[]
body_pts+=bez((329,454),(343,417),(372,392),(421,377),18)
body_pts+=bez((421,377),(484,360),(558,360),(614,379),18)
body_pts+=bez((614,379),(654,392),(680,414),(693,439),16)
body_pts+=bez((693,439),(680,463),(656,486),(615,500),16)
body_pts+=bez((615,500),(555,524),(488,536),(440,523),18)
body_pts+=bez((440,523),(393,512),(356,487),(342,466),16)
body_pts+=bez((342,468),(334,462),(330,458),(329,454),10)

# ruffled caudal veil - broad, asymmetrical, trailing right
tail_pts=[(681,419),(701,384),(739,342),(781,311),(820,298),(847,315),(866,351),
          (885,330),(911,322),(936,351),(950,397),(970,380),(994,407),(1005,439),
          (992,463),(1012,479),(991,506),(962,532),(933,558),(907,579),(876,602),
          (846,616),(817,609),(792,591),(773,562),(756,534),(741,548),(722,516),
          (709,486),(699,460),(686,439)]
# dorsal sail
dorsal_pts=[(470,371),(480,342),(500,317),(526,297),(557,279),(591,267),(621,268),
            (648,283),(672,302),(695,326),(721,344),(737,355),(725,378),(699,392),
            (672,396),(642,392),(612,386),(580,379),(548,374),(515,371)]
# anal curtain
anal_pts=[(452,516),(465,547),(489,575),(516,602),(548,629),(581,654),(613,670),
          (646,673),(677,662),(704,640),(729,614),(748,588),(758,560),(746,545),
          (720,551),(694,547),(671,535),(649,521),(624,512),(595,505),(564,508),
          (531,513),(498,516)]
# twin ventral streamers
pelvic_a=[(418,502),(407,532),(393,565),(383,603),(377,645),(386,675),(401,686),
          (413,671),(409,640),(416,607),(431,575),(441,541),(440,511)]
pelvic_b=[(438,507),(447,542),(458,581),(471,621),(487,659),(505,682),(521,681),
          (523,660),(508,630),(496,594),(486,556),(477,519),(463,501)]
# pectoral fan (front of gill)
pect_pts=[(427,438),(447,431),(470,438),(493,453),(511,474),(522,498),
          (515,514),(497,516),(480,501),(462,484),(446,468),(432,453)]

body_mask=mask_polys([body_pts],0.7)
tail_mask=mask_polys([tail_pts],0.8)
dorsal_mask=mask_polys([dorsal_pts],0.7)
anal_mask=mask_polys([anal_pts],0.7)
pect_mask=mask_polys([pect_pts],0.5)
pelvic_mask=mask_polys([pelvic_a,pelvic_b],0.5)
fish_mask=mask_polys([body_pts,tail_pts,dorsal_pts,anal_pts,pelvic_a,pelvic_b],1.0)

# ---------------------------------------------------------------- aura / dark reserve behind fish
poly("fish_aura",ellipse_pts(646,455,340,190,44),(0,8,13),(0,8,13),size=86,spacing=35,
     opacity=0.055,preset="airbrush",hardness=0.08,jitter=0.4,edge=False,seed=93)
# a red-tinted breath behind the caudal veil
poly("fish_aura",ellipse_pts(820,444,190,155,40),(47,12,19),(28,9,18),size=60,spacing=30,
     opacity=0.055,preset="airbrush",hardness=0.10,jitter=0.4,edge=False,seed=94)

# ---------------------------------------------------------------- fin membranes
# dorsal: deep base, warm lit upper edge
poly("dorsal",dorsal_pts,(83,21,38),(188,47,46),size=16,spacing=5.8,opacity=0.91,
     preset="sponge",hardness=0.62,jitter=0.38,edge=False,gradient_angle=90,seed=101)
# broad membrane strokes, fanning out of the back
dorsal_mask_b=dorsal_mask>0
for i in range(760):
    x,y=shape_sample(dorsal_mask,1)[0]
    bx,by=568,374
    ang=math.atan2(y-by,x-bx)+rng.uniform(-0.08,0.08)
    t=np.clip((by-y)/120,0,1)
    c=mix((91,21,37),(207,66,52),t**0.85)
    c=mix(c,(244,112,66),max(0,(t-0.72))*1.2)
    c+=rng.uniform(-8,8,3)
    add("dorsal",curved(x,y,rng.uniform(18,55),ang,rng.uniform(-4,4),3),
        rng.uniform(5,14),jc(c,7),rng.uniform(0.30,0.72),
        rng.choice(["bristle","dry","round_soft","sponge"]),rng.uniform(0.4,0.85))
# fin rays terminate inside the membrane, so no paint quills escape the silhouette
for i in range(110):
    bx=502+rng.uniform(-7,7); by=373+rng.uniform(-2,3)
    ex,ey=shape_sample(dorsal_mask,1)[0]
    if math.hypot(ex-bx,ey-by)<62: continue
    x1=bx+(ex-bx)*0.48+rng.uniform(-4,4); y1=by+(ey-by)*0.48+rng.uniform(-2,2)
    op=rng.uniform(0.15,0.46)
    add("dorsal",[(bx,by),(x1,y1),(ex,ey)],rng.uniform(1.0,2.9),jc((214,76,58),9),op,
        rng.choice(["dry","liner","round_hard"]),rng.uniform(0.45,0.82),1.0)
edge_rim("dorsal",dorsal_mask,(112,27,39),(248,128,87),lit_test=lambda x,y:y<330 or (y<358 and x>600),
         step=7,size_min=1.8,size_max=5.2,op=0.40)

# anal: darker velvet curtain with hot trailing edge
poly("anal",anal_pts,(77,18,35),(161,38,43),size=16,spacing=5.8,opacity=0.92,
     preset="sponge",hardness=0.62,jitter=0.4,edge=False,gradient_angle=90,seed=111)
for i in range(900):
    x,y=shape_sample(anal_mask,1)[0]
    bx,by=592,506
    ang=math.atan2(y-by,x-bx)+rng.uniform(-0.08,0.08)
    t=np.clip((y-by)/155,0,1)
    c=mix((78,17,32),(171,42,44),t**0.8)
    c=mix(c,(220,69,58),max(0,(t-0.68))*1.1)
    c+=rng.uniform(-8,8,3)
    add("anal",curved(x,y,rng.uniform(18,52),ang,rng.uniform(-4,4),3),
        rng.uniform(5,14),jc(c,7),rng.uniform(0.28,0.70),
        rng.choice(["bristle","dry","round_soft","sponge"]),rng.uniform(0.42,0.86))
for i in range(125):
    bx=575+rng.uniform(-18,18); by=504+rng.uniform(-3,3)
    ex,ey=shape_sample(anal_mask,1)[0]
    if math.hypot(ex-bx,ey-by)<72: continue
    x1=bx+(ex-bx)*0.48+rng.uniform(-4,4); y1=by+(ey-by)*0.48+rng.uniform(-2,2)
    add("anal",[(bx,by),(x1,y1),(ex,ey)],rng.uniform(0.9,2.8),
        jc((201,65,56) if y1>530 else (136,34,47),9),rng.uniform(0.14,0.42),
        rng.choice(["dry","liner","round_hard"]),rng.uniform(0.4,0.8),1.0)
edge_rim("anal",anal_mask,(98,22,36),(222,82,65),lit_test=lambda x,y:y>558 and x>545,
         step=8,size_min=1.6,size_max=5.0,op=0.38)

# caudal veil: translucent, directional fan
poly("tail",tail_pts,(91,18,39),(176,45,47),size=21,spacing=7.0,opacity=0.78,
     preset="sponge",hardness=0.58,jitter=0.44,edge=False,gradient_angle=0,seed=121)
# mottled membrane, radial brush direction
for i in range(1900):
    x,y=shape_sample(tail_mask,1)[0]
    bx,by=699,451
    dx=x-bx; dy=y-by; r=math.hypot(dx,dy)
    ang=math.atan2(dy,dx)+rng.uniform(-0.11,0.11)
    t=np.clip(r/330,0,1)
    c=mix((84,16,37),(190,48,49),t**0.9)
    # palette inflection: only the upper rim catches coral; lower veil stays wine-dark
    if y<408:
        c=mix(c,(238,100,70),np.clip((408-y)/105,0,1)*0.56)
    elif y>500:
        c=mix(c,(119,25,45),np.clip((y-500)/125,0,1)*0.55)
    c+=rng.uniform(-10,10,3)
    add("tail",curved(x,y,rng.uniform(25,100),ang,rng.uniform(-8,8),4),
        rng.uniform(5,18),jc(c,8),rng.uniform(0.26,0.72),
        rng.choice(["bristle","dry","round_soft","sponge","chalk"]),rng.uniform(0.38,0.86))
# radial fin rays: long, broken, slightly curved
for i in range(260):
    a=rng.uniform(-1.10,1.05)
    r0=rng.uniform(32,105); r1=rng.uniform(170,342)
    bx,by=700,451
    # prevent rays outside mask by shrinking endpoints until inside
    for _ in range(8):
        ex=bx+math.cos(a)*r1; ey=by+math.sin(a)*r1
        if 0<=int(ex)<W and 0<=int(ey)<H and tail_mask[int(ey),int(ex)]>0: break
        r1*=0.93
    x0=bx+math.cos(a)*r0; y0=by+math.sin(a)*r0
    xm=(x0+ex)*0.5 + math.cos(a+math.pi/2)*rng.uniform(-10,10)
    ym=(y0+ey)*0.5 + math.sin(a+math.pi/2)*rng.uniform(-10,10)
    lit=ey<445 or (a<0 and ey<490)
    cc=mix((128,29,46),(250,124,90),rng.uniform(0.3,0.9)) if lit else mix((107,23,43),(194,52,53),rng.uniform(0.2,0.8))
    add("tail_ray",[(x0,y0),(xm,ym),(ex,ey)],rng.uniform(1.0,3.7),jc(cc,8),
        rng.uniform(0.13,0.46),rng.choice(["dry","liner","round_hard"]),rng.uniform(0.35,0.82),1.0)
# a few soft folds that cross the fan, suggesting silk-like pleats
for i in range(120):
    x,y=shape_sample(tail_mask,1)[0]
    bx,by=700,451
    a=math.atan2(y-by,x-bx)+rng.uniform(-0.16,0.16)
    add("tail",curved(x,y,rng.uniform(60,145),a,rng.uniform(-16,16),5),
        rng.uniform(2.5,8),jc(mix((78,15,33),(148,35,45),rng.random()),8),
        rng.uniform(0.08,0.26),rng.choice(["dry","bristle","chalk"]),rng.uniform(0.3,0.7),1.0)
# broken coral glints only where the veil catches the upper water
edge_rim("tail_edge",tail_mask,(90,19,34),(250,141,104),
         lit_test=lambda x,y:(y<392 and x>742) or (x>974 and 414<y<474) or (y<365 and x>690),
         step=8,size_min=1.7,size_max=5.4,op=0.40)
edge_rim("tail_edge",tail_mask,(69,12,27),(202,70,66),
         lit_test=lambda x,y:(y>532 and x>808),
         step=9,size_min=1.3,size_max=4.0,op=0.28)
# sparse deep edge definition, not a drawn outline
edge_rim("tail_edge",tail_mask,(50,10,24),(89,22,40),lit_test=None,
         step=12,size_min=1.0,size_max=3.4,op=0.16)

# pelvic streamers
poly("pelvic",pelvic_a,(91,22,38),(178,41,43),size=10,spacing=4.2,opacity=0.93,
     preset="bristle",hardness=0.58,jitter=0.3,edge=False,gradient_angle=90,seed=131)
poly("pelvic",pelvic_b,(89,20,37),(196,46,44),size=10,spacing=4.2,opacity=0.93,
     preset="bristle",hardness=0.58,jitter=0.3,edge=False,gradient_angle=90,seed=132)
for i in range(310):
    x,y=shape_sample(pelvic_mask,1)[0]
    ang=math.radians(94+rng.uniform(-16,16))
    c=mix((101,24,38),(221,66,55),rng.uniform(0.1,1.0))
    add("pelvic",curved(x,y,rng.uniform(12,42),ang,rng.uniform(-4,4),3),
        rng.uniform(3,9),jc(c,8),rng.uniform(0.22,0.62),
        rng.choice(["dry","bristle","round_soft"]),rng.uniform(0.38,0.82))
for i in range(80):
    x,y=shape_sample(pelvic_mask,1)[0]
    add("pelvic",[(x,y),(x+rng.uniform(-2,2),y+rng.uniform(10,30))],
        rng.uniform(0.8,2.2),jc((242,105,78),8),rng.uniform(0.12,0.40),
        rng.choice(["liner","dry"]),0.55,1.0)
edge_rim("pelvic",pelvic_mask,(96,23,38),(245,112,81),
         lit_test=lambda x,y:x<435,step=5,size_min=1.4,size_max=4.5,op=0.52)

# ---------------------------------------------------------------- body
poly("body",body_pts,(97,23,39),(177,45,46),size=18,spacing=6.0,opacity=0.96,
     preset="sponge",hardness=0.65,jitter=0.36,edge=False,gradient_angle=0,seed=141)
# form-shadow base: belly / rear
for i in range(1250):
    x,y=shape_sample(body_mask,1)[0]
    u=(x-340)/(695-340); v=(y-378)/(530-378)
    up=np.clip((430-y)/62,0,1); lo=np.clip((y-452)/74,0,1)
    base=mix((97,23,39),(181,48,46),np.clip(u*0.85+0.12,0,1))
    base=mix(base,(224,82,55),up*0.72)
    base=mix(base,(68,19,37),lo*0.68)
    # cool reflected bounce at the very bottom of the chest
    base=mix(base,(51,47,53),np.clip((y-500)/34,0,1)*0.45)
    base+=rng.uniform(-9,9,3)
    ang=-0.07+0.20*math.sin((y-440)/65)+0.07*math.sin(x/95)+rng.uniform(-0.06,0.06)
    ln=rng.uniform(12,48)
    sz=rng.uniform(5,15)
    add("body_form",curved(x,y,ln,ang,rng.uniform(-7,7),4),sz,jc(base,7),
        rng.uniform(0.25,0.71),rng.choice(["bristle","dry","round_soft","sponge"]),
        rng.uniform(0.38,0.86))
# deep shadow band along belly and beneath gill
for i in range(520):
    x,y=shape_sample(body_mask,1)[0]
    if y < 466 and x>430: continue
    lo=np.clip((y-460)/70,0,1)
    c=mix((70,17,35),(124,32,42),rng.random()*0.55)
    add("body_shadow",curved(x,y,rng.uniform(20,58),-0.03+rng.uniform(-0.12,0.12),rng.uniform(-5,5),3),
        rng.uniform(4,13),jc(c,7),rng.uniform(0.12,0.42)*lo,
        rng.choice(["bristle","dry","round_soft"]),rng.uniform(0.32,0.78))
# hot lit back / shoulder
for i in range(760):
    x,y=shape_sample(body_mask,1)[0]
    lit=np.clip((438-y)/62,0,1)*np.clip((x-360)/220,0,1)
    if lit<0.12 and rng.random()>0.14: continue
    c=mix((181,46,45),(242,111,69),lit*rng.uniform(0.45,1.0))
    c=mix(c,(253,163,103),max(0,lit-0.65)*0.7)
    add("body_light",curved(x,y,rng.uniform(8,32),-0.12+rng.uniform(-0.10,0.08),rng.uniform(-3,3),3),
        rng.uniform(3,10),jc(c,7),rng.uniform(0.16,0.52)*min(1,lit+0.22),
        rng.choice(["dry","bristle","round_hard"]),rng.uniform(0.45,0.9))
# contour: dark rim then selective warm rim
edge_rim("body",body_mask,(63,15,29),(91,23,37),lit_test=None,step=5,
         size_min=1.8,size_max=5.0,op=0.33)
edge_rim("body",body_mask,(117,30,42),(251,148,104),
         lit_test=lambda x,y:(y<430 and x<625) or (x<380 and y<470),
         step=5,size_min=2.2,size_max=6.2,op=0.58)

# scale crescents
for row,y in enumerate(np.arange(388,520,10.5)):
    xoff=7.0 if row%2 else 0.0
    for x in np.arange(414+xoff,672,14.5):
        px=x+rng.uniform(-3.0,3.0); py=y+rng.uniform(-2.0,2.0)
        xi,yi=int(px),int(py)
        if not (0<=xi<W and 0<=yi<H and body_mask[yi,xi]>0): continue
        if px<420 or px>665: continue
        if rng.random()<0.22: continue
        # skip eye and gill area
        if (px-382)**2/380+(py-430)**2/330<1: continue
        t=np.clip((430-py)/80,0,1)
        base=mix((121,31,42),(224,93,63),t*0.7+rng.uniform(-0.12,0.12))
        add("scales",[(px-5.6,py+rng.uniform(-0.4,0.4)),(px,py+2.4),(px+5.6,py+rng.uniform(-0.6,0.4))],
            rng.uniform(1.8,3.3),jc(base,7),rng.uniform(0.22,0.52),
            rng.choice(["liner","dry","round_soft"]),rng.uniform(0.38,0.72),0.9)
        if rng.random()<0.62:
            add("scales",[(px-4.8,py+2.7),(px,py+4.6),(px+5.0,py+2.8)],
                rng.uniform(1.1,2.2),jc((91,22,37),5),rng.uniform(0.14,0.34),
                rng.choice(["liner","dry"]),0.55,0.85)

# body painterly ridges following the flank
for i in range(90):
    x,y=shape_sample(body_mask,1)[0]
    if x<405: continue
    add("body_light",curved(x,y,rng.uniform(28,78),-0.05+rng.uniform(-0.08,0.05),rng.uniform(-3,3),4),
        rng.uniform(1.1,2.6),jc(mix((242,130,94),(255,190,133),rng.random()),6),
        rng.uniform(0.10,0.28),rng.choice(["dry","chalk","liner"]),rng.uniform(0.3,0.65),1.0)

# ---------------------------------------------------------------- head, eye, gill, mouth
head_mask=mask_polys([[(329,454),(341,419),(366,394),(407,381),(446,399),(454,438),
                        (442,479),(410,503),(370,497),(344,478),(332,464)]],0.55)
poly("head",[(329,454),(341,419),(366,394),(407,381),(446,399),(454,438),(442,479),
             (410,503),(370,497),(344,478),(332,464)],(95,23,40),(172,44,44),size=12,spacing=4.5,
     opacity=0.57,preset="sponge",hardness=0.62,jitter=0.3,edge=False,gradient_angle=90,seed=151)
# head/cheek shadow and forehead highlights
for i in range(650):
    x,y=shape_sample(head_mask,1)[0]
    up=np.clip((424-y)/44,0,1)
    c=mix((101,25,40),(208,68,51),up*0.7)
    c=mix(c,(244,123,79),max(0,up-0.68)*0.8)
    if y>463: c=mix(c,(75,19,36),np.clip((y-463)/38,0,1)*0.55)
    c+=rng.uniform(-7,7,3)
    ang=-0.10+0.23*math.sin((y-430)/45)+rng.uniform(-0.09,0.09)
    add("head",curved(x,y,rng.uniform(9,30),ang,rng.uniform(-3,3),3),
        rng.uniform(3,10),jc(c,7),rng.uniform(0.18,0.58),
        rng.choice(["bristle","dry","round_hard","round_soft"]),rng.uniform(0.4,0.85))
# gill plate: dark crease with warm rim behind it
gill=bez((414,389),(431,404),(427,449),(408,492),28)
for i in range(len(gill)-5):
    if i%2: continue
    x,y=gill[i]; x2,y2=gill[i+5]
    add("head",[(x,y),(x2,y2)],rng.uniform(2.4,4.5),jc((59,14,28),5),rng.uniform(0.28,0.58),
        rng.choice(["round_hard","dry"]),0.7,1.0)
for i in range(18):
    j=int(rng.integers(6,len(gill)-8)); x,y=gill[j]
    add("body_light",[(x+2,y),(x+5,y+8),(x+2,y+15)],rng.uniform(1.2,2.6),
        jc((236,111,77),7),rng.uniform(0.10,0.30),rng.choice(["dry","liner"]),0.55,1.0)
# operculum edge catchlight
for i in range(35):
    t=rng.random(); y=392+t*97; x=413.5+6*math.sin(t*math.pi*1.2)+rng.uniform(-1.5,1.5)
    add("body_light",[(x,y),(x+1.0,y+rng.uniform(4,9))],rng.uniform(1.0,2.5),
        jc((248,147,101),7),rng.uniform(0.12,0.32),rng.choice(["dry","liner"]),0.55,1.0)

# mouth
mouth=bez((330,456),(338,462),(350,466),(363,463),16)
for i in range(len(mouth)-3):
    x,y=mouth[i]; x2,y2=mouth[i+3]
    add("head",[(x,y),(x2,y2)],rng.uniform(1.8,3.5),jc((56,10,22),5),rng.uniform(0.35,0.68),
        "round_hard",0.72,1.0)
add("head",curved(332,460,16,0.30,3,3),3.0,(177,62,52),0.50,"round_soft",0.55,1.0)
add("head",curved(333,449,22,-0.20,2,3),2.5,(236,113,75),0.32,"dry",0.5,1.0)
for i in range(10):
    x=333+rng.uniform(0,21); y=460+0.32*(x-333)+rng.uniform(-1,1)
    add("body_light",[(x,y),(x+4,y+1.4)],rng.uniform(0.9,2.0),jc((255,165,118),6),
        rng.uniform(0.12,0.34),"liner",0.55,1.0)

# eye socket, iris, pupil, wet catches
poly("head",ellipse_pts(381,431,18.0,16.5,28),(58,13,26),(93,23,35),size=6.4,spacing=2.6,
     opacity=0.92,preset="round_soft",hardness=0.55,jitter=0.16,edge=True,seed=161)
poly("head",ellipse_pts(381,431,11.8,11.2,24),(143,93,40),(219,165,81),size=4.0,spacing=1.9,
     opacity=0.98,preset="round_soft",hardness=0.55,jitter=0.10,edge=True,seed=162)
poly("head",ellipse_pts(381,431,6.1,7.0,22),(8,7,10),(18,13,16),size=3.0,spacing=1.4,
     opacity=1.0,preset="round_hard",hardness=0.7,jitter=0.06,edge=False,seed=163)
# iris radial flecks
for i in range(38):
    a=rng.uniform(0,math.tau); r=rng.uniform(6,11)
    x=381+math.cos(a)*r; y=431+math.sin(a)*r
    add("head",[(x,y),(x+math.cos(a)*rng.uniform(1.5,4),y+math.sin(a)*rng.uniform(1.5,4))],
        rng.uniform(0.7,1.9),jc(mix((206,149,65),(255,204,111),rng.random()),7),
        rng.uniform(0.18,0.55),"liner",0.5,1.0)
# eyelid and brow
for i in range(24):
    t=rng.random(); a=math.pi*(0.08+0.84*t)
    x=381+math.cos(a)*rng.uniform(12.5,18); y=431-math.sin(a)*rng.uniform(10,16)
    add("head",[(x-2.5,y+0.8),(x,y-1.5),(x+3.2,y+0.8)],rng.uniform(1.7,4.0),jc((51,11,24),5),
        rng.uniform(0.28,0.62),rng.choice(["round_hard","dry"]),0.7,1.0)
# eye catchlights (screen)
poly("glints",ellipse_pts(376,426,3.2,3.0,14),(255,247,223),None,size=2.0,spacing=1.1,
     opacity=0.95,preset="round_hard",hardness=0.75,jitter=0.04,edge=False,seed=164)
poly("glints",ellipse_pts(384,434,1.4,1.4,10),(255,209,177),None,size=1.2,spacing=0.9,
     opacity=0.65,preset="round_hard",hardness=0.6,jitter=0.04,edge=False,seed=165)

# ---------------------------------------------------------------- pectoral fin (front, translucent)
poly("pectoral",pect_pts,(151,42,48),(214,73,61),size=9,spacing=4.0,opacity=0.085,
     preset="round_soft",hardness=0.40,jitter=0.28,edge=False,gradient_angle=0,seed=171)
# translucent lamellae: paint short, separated strokes so the flank reads through
for i in range(300):
    x,y=shape_sample(pect_mask,1)[0]
    bx,by=432,449
    a=math.atan2(y-by,x-bx)+rng.uniform(-0.10,0.10)
    c=mix((168,47,49),(238,106,78),rng.uniform(0.20,1.0))
    add("pectoral",curved(x,y,rng.uniform(7,24),a,rng.uniform(-2,2),3),
        rng.uniform(2.2,6.5),jc(c,6),rng.uniform(0.06,0.24),
        rng.choice(["dry","round_soft","bristle","chalk"]),rng.uniform(0.32,0.72))
for i in range(28):
    bx=432+rng.uniform(-3,3); by=449+rng.uniform(-2,2)
    a=math.radians(rng.uniform(4,78)); r=rng.uniform(48,96)
    ex=bx+math.cos(a)*r; ey=by+math.sin(a)*r
    if not (0<=int(ex)<W and 0<=int(ey)<H and pect_mask[int(ey),int(ex)]>0):
        r*=0.87; ex=bx+math.cos(a)*r; ey=by+math.sin(a)*r
    add("pectoral",[(bx,by),(bx+(ex-bx)*0.55+rng.uniform(-2,2),by+(ey-by)*0.55),(ex,ey)],
        rng.uniform(0.7,1.8),jc((242,126,94),7),rng.uniform(0.06,0.22),
        rng.choice(["dry","liner"]),0.5,1.0)
edge_rim("pectoral",pect_mask,(120,31,42),(247,143,105),
         lit_test=lambda x,y:(x>490 and y>475) or (y>505),step=8,size_min=1.0,size_max=3.2,op=0.26)

# ---------------------------------------------------------------- impasto highlights on fish
# broken wet ridges on the back and shoulder
for i in range(220):
    x,y=shape_sample(body_mask,1)[0]
    if y>455 or x<410: continue
    add("fish_light",curved(x,y,rng.uniform(8,34),-0.08+rng.uniform(-0.10,0.08),rng.uniform(-3,3),3),
        rng.uniform(1.1,3.4),jc(mix((249,142,97),(255,199,143),rng.random()),6),
        rng.uniform(0.08,0.34),rng.choice(["dry","chalk","liner"]),rng.uniform(0.25,0.65),1.0)
# a few decisive warm contour hits along the nape and dorsal root
for i in range(80):
    t=rng.random(); x=430+t*250; y=378+8*math.sin(t*math.pi*1.4)+rng.uniform(-4,4)
    if body_mask[int(y),int(x)]==0: continue
    add("fish_light",[(x-8,y+rng.uniform(-1,1)),(x,y+rng.uniform(-2,2)),(x+8,y+rng.uniform(-1,1))],
        rng.uniform(1.4,4.0),jc((255,173,115),5),rng.uniform(0.12,0.42),
        rng.choice(["dry","bristle","round_hard"]),rng.uniform(0.45,0.85),1.0)
# bright flecks around cheek and fin roots
for i in range(90):
    x=rng.uniform(352,470); y=rng.uniform(389,493)
    if body_mask[int(y),int(x)]==0: continue
    add("fish_light",[(x,y),(x+rng.uniform(1,6),y+rng.uniform(-1,1))],
        rng.uniform(1.0,2.8),jc((255,164,119),6),rng.uniform(0.10,0.38),
        rng.choice(["dry","chalk","liner"]),0.55,1.0)
# fine specular scales on lit flank
for i in range(170):
    x=rng.uniform(430,645); y=rng.uniform(390,458)
    if body_mask[int(y),int(x)]==0: continue
    add("fin_glints",[(x-2,y),(x,y-1.5),(x+2.5,y)],rng.uniform(0.8,2.4),
        jc((255,199,152),5),rng.uniform(0.08,0.28),
        rng.choice(["round_soft","chalk","liner"]),0.35,1.0)

# ---------------------------------------------------------------- final silhouette cleanup and fin roots
# a last sparse contour keeps the painterly fringe from becoming fur
edge_rim("contour",body_mask,(53,11,24),(103,25,38),lit_test=None,step=9,
         size_min=0.9,size_max=3.4,op=0.16)
edge_rim("contour",body_mask,(112,28,41),(249,145,101),
         lit_test=lambda x,y:(y<421 and 380<x<650) or (x<378 and 415<y<470),
         step=7,size_min=1.4,size_max=4.0,op=0.32)
# peduncle: a dark hinge separates the body from the caudal veil
for i in range(60):
    t=rng.random(); x=680+12*math.sin(t*math.pi)+rng.uniform(-5,5); y=414+t*75+rng.uniform(-3,3)
    add("contour",[(x-2,y),(x+2,y+rng.uniform(5,12))],rng.uniform(1.8,5.0),
        jc((74,15,32),6),rng.uniform(0.10,0.30),rng.choice(["dry","bristle"]),0.5,1.0)
for i in range(26):
    t=rng.random(); x=674+rng.uniform(-5,6); y=407+t*30
    add("contour",[(x,y),(x+4,y+1.0)],rng.uniform(1.2,3.2),jc((248,137,96),6),
        rng.uniform(0.10,0.30),rng.choice(["dry","liner"]),0.5,1.0)
# anchor the dorsal and anal roots with a warm-dark seam
for i in range(55):
    x=rng.uniform(480,650); y=373+rng.uniform(-4,5)
    if body_mask[int(np.clip(y,0,H-1)),int(np.clip(x,0,W-1))]==0: continue
    add("contour",[(x-5,y),(x+5,y+rng.uniform(-1,1))],rng.uniform(1.4,3.6),
        jc((83,19,35),6),rng.uniform(0.08,0.26),"dry",0.5,1.0)
for i in range(55):
    x=rng.uniform(470,650); y=507+rng.uniform(-5,6)
    if body_mask[int(np.clip(y,0,H-1)),int(np.clip(x,0,W-1))]==0: continue
    add("contour",[(x-5,y),(x+5,y+rng.uniform(-1,1))],rng.uniform(1.4,3.6),
        jc((65,13,29),6),rng.uniform(0.08,0.26),"dry",0.5,1.0)

# ---------------------------------------------------------------- foreground bubbles and specks
bubble_groups=[(930,150,9),(1040,255,7),(820,105,6),(720,225,5),(1110,430,5),
               (510,145,5),(620,110,4),(980,610,4),(330,215,3)]
for bx,by,count in bubble_groups:
    for j in range(count):
        x=bx+rng.normal(0,28); y=by+rng.normal(0,75)
        r=rng.uniform(2.2,10.5)
        if rng.random()<0.78:
            pts=ellipse_pts(x,y,r,r*rng.uniform(0.9,1.15),18)
            op=rng.uniform(0.20,0.62)
            add("bubbles",pts+[pts[0]],max(0.9,r*0.30),jc((122,215,194),8),op,
                rng.choice(["liner","round_soft"]),0.55,1.0)
            add("bubbles",[(x-r*0.35,y-r*0.32),(x-r*0.05,y-r*0.62)],max(0.8,r*0.20),
                (225,255,236),min(0.9,op+0.18),rng.choice(["round_hard","liner"]),0.7,1.0)
# suspended particulate - uneven, with a brighter near-camera drift
for i in range(620):
    x=rng.uniform(0,W); y=rng.uniform(0,H)
    # fewer specks over the body so the silhouette stays clean
    if 300<x<1030 and 280<y<700 and rng.random()<0.60: continue
    r=rng.uniform(0.35,2.0)
    add("particles",[(x,y),(x+rng.uniform(-1.4,1.4),y+rng.uniform(-1.4,1.4))],r,
        jc(mix((77,159,149),(185,232,208),rng.random()*0.6),7),rng.uniform(0.05,0.28),
        rng.choice(["round_soft","chalk","liner"]),0.4,1.0)
# a handful of blurry bokeh specks for depth
for i in range(26):
    x=rng.uniform(0,W); y=rng.uniform(0,H)
    add("particles",[(x,y)],rng.uniform(5,16),(83,176,162),rng.uniform(0.015,0.055),
        "airbrush",0.12,1.0)

# ---------------------------------------------------------------- final atmosphere
# cool upper-left bloom and some red bounce in water beneath fish
poly("glints",ellipse_pts(155,105,230,155,36),(67,151,144),None,size=72,spacing=28,
     opacity=0.025,preset="airbrush",hardness=0.12,jitter=0.35,edge=False,seed=181)
poly("glints",ellipse_pts(758,651,185,80,36),(133,45,47),None,size=58,spacing=26,
     opacity=0.022,preset="airbrush",hardness=0.14,jitter=0.35,edge=False,seed=182)
for i in range(34):
    x=rng.uniform(0,W); y=rng.uniform(50,850)
    add("glints",curved(x,y,rng.uniform(2,10),rng.uniform(0,math.tau),1,2),
        rng.uniform(0.7,1.8),jc((177,235,213),6),rng.uniform(0.10,0.35),
        "round_hard",0.7,1.0)

# soft painted vignette; broad softlight strokes at the edges only
for i in range(260):
    side=rng.integers(0,4)
    if side==0: x=rng.uniform(-80,240); y=rng.uniform(-80,H+40)
    elif side==1: x=rng.uniform(980,W+80); y=rng.uniform(-80,H+40)
    elif side==2: x=rng.uniform(-80,W+80); y=rng.uniform(-80,120)
    else: x=rng.uniform(-80,W+80); y=rng.uniform(760,H+80)
    add("vignette",curved(x,y,rng.uniform(60,180),rng.uniform(-0.28,0.10),rng.uniform(-12,12),4),
        rng.uniform(35,95),(6,28,31),rng.uniform(0.015,0.055),
        rng.choice(["airbrush","round_soft"]),0.12,1.0)

n=plan_io.dump_plan(str(OUT/"plan.json"),W,H,doc["strokes"],
    layer_blends=BLENDS,layer_order=LAYER_ORDER)
print({"plan":str(OUT/"plan.json"),"strokes":n,"layers":len(LAYER_ORDER)})
