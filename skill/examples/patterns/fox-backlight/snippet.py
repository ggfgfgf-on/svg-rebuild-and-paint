# fox-backlight: backlit forest layers + rim-lit subject
LAYERS = ["base","sky","dawn_glow","distant_forest","back_mist",
          "mid_forest","trunks","ground","ground_detail","floor_mist",
          "boulder","boulder_detail","moss",
          "fox_under","fox_coat","fox_detail","fox_rim","foreground","atmosphere"]

# distant forest: low contrast, mist-eaten bases
for i in range(140):
    x = rng.uniform(-40, W+40); top = rng.uniform(80, 300)
    add("distant_forest", [(x,top),(x+rng.uniform(-8,8), HORIZON+90)],
        rng.uniform(12,32), mix((46,58,52),(92,100,82), rng.random()), 0.45, "dry")

# fog between planes, never one flat wash
for cx,cy,rx,ry,n,op in [(420,430,380,120,180,0.06),(810,495,330,120,180,0.05)]:
    fog_cloud("back_mist", cx,cy,rx,ry,n,(180,178,164),(205,198,180),20,60,op)

# fox: under -> coat -> detail -> rim (screen)
fill_poly("fox_under", fox_body, (62,40,32), size=18, spacing=5)
fill_poly("fox_coat", fox_body, (176,92,46), (210,130,66), size=9, spacing=4)
for i in range(900):  # fur direction follows body, not random
    p = sample_poly(fox_body)
    add("fox_detail", [(p[0],p[1]),(p[0]+rng.uniform(-6,6),p[1]+rng.uniform(4,18))],
        rng.uniform(1.5,4), mix((232,152,82),(120,60,38),rng.random()), 0.35, "dry")
taper("fox_rim", lit_edge, (255,206,144), 4.5, 1.0, steps=24, opacity=0.35)
