# sunset-soft: opaque sky gradient -> soft cloud shelf -> narrow sun column
SKY_STOPS = [(0.00,(34,40,72)),(0.35,(92,72,112)),(0.62,(198,112,104)),
             (0.82,(246,166,104)),(1.00,(255,206,142))]

def sky_color(y, x):
    t = clamp(y / HORIZON)
    c = color_at(SKY_STOPS, t)
    warm = math.exp(-((x-SUNX)/520.0)**2 - ((y-HORIZON)/330.0)**2)
    return mix(c, (255,196,128), 0.28*warm)

for y in np.linspace(-80, HORIZON+90, 9):
    for xc in np.arange(-80, W+80, 90):
        add("base_sky", [(-150,y+2), (W*0.52,y-2), (W+150,y+1)],
            rng.uniform(40,70), sky_color(min(y,HORIZON),xc), 0.75, "flat", 0.25)

# cloud shelf: long, low-opacity, no hard outline
for i in range(420):
    y = HORIZON - rng.uniform(40, 210)
    ln = rng.uniform(60, 260)
    c = mix(sky_color(y, rng.uniform(0,W)), (210,170,170), rng.uniform(0.1,0.3))
    add("clouds", [(x,y),(x+ln,y+rng.uniform(-6,6))],
        rng.uniform(22,55), c, rng.uniform(0.06,0.16), "dry", 0.2)

# sun glow: many large faint dabs, then a narrow reflection ladder
add("sun_glow", [(SUNX,SUNY)], 260, (255,224,170), 0.10, "round_soft")
for i in range(260):
    x = SUNX + rng.uniform(-70,70); y = HORIZON + rng.uniform(10,360)
    add("reflection", [(x-rng.uniform(5,26),y),(x+rng.uniform(5,26),y)],
        rng.uniform(1.5,6), (255,204,148), rng.uniform(0.03,0.12), "flat", 0.2)
