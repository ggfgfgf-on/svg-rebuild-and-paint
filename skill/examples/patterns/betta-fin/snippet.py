# betta-fin: membrane layers -> tapered rays -> lit edge
LAYERS = ["under","water","water_deep","rays","caustic","far_flora","rocks",
          "kelp","fish_aura","dorsal","anal","tail","tail_ray","tail_edge",
          "pelvic","body","body_form","body_shadow","body_light","scales",
          "head","pectoral","fin_glints","fish_light","contour","bubbles","particles"]

# membrane first: broad, translucent, not one flat red
fill_poly("tail", tail_shape, (142,34,30), size=16, spacing=5, opacity=0.82)
fill_poly("dorsal", dorsal_shape, (168,44,34), size=10, spacing=4, opacity=0.78)

# rays: tapered, radiating from the base, not equal width
for i in range(180):
    a = base_angle + rng.uniform(-0.55,0.55)
    p0 = fin_base; p1 = fin_base + (math.cos(a)*rng.uniform(80,230), math.sin(a)*rng.uniform(80,230))
    tapered("tail_ray", [p0,p1], mix((214,78,52),(120,22,24),rng.random()),
            rng.uniform(2.0,5.5), 0.4, steps=16, opacity=0.55)

# lit edge: only the outer 15-25% of the fin
for p in edge_samples(tail_shape, 220):
    add("tail_edge", [p, (p[0]+rng.uniform(2,8), p[1]+rng.uniform(-2,2))],
        rng.uniform(1.0,3.2), (246,116,82), rng.uniform(0.12,0.35), "liner")

# water: sparse caustic, never a full-screen texture
for i in range(900):
    add("caustic", curved_pts(...), rng.uniform(1,4), (120,190,170), rng.uniform(0.02,0.08), "liner")
