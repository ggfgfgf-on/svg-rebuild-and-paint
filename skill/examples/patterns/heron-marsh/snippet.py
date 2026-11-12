# heron-marsh: silhouette -> form -> feathers -> detail -> rim
LAYERS = ["sky_tones","dawn_clouds","mist","water_tones","water_sheen",
          "reeds_far","reflection","heron_legs","heron_silhouette","heron_form",
          "heron_feathers","heron_detail","heron_rim","surface","reeds_mid",
          "reeds_front"]

# silhouette first, then lighter form inside it
fill_poly("heron_silhouette", body_poly, (58,66,64), size=14, spacing=5)
fill_poly("heron_form", body_poly, (178,180,168), (204,204,190), size=8, spacing=4)

# neck: one tapered polyline, thinner at the head
taper("heron_form", neck_pts, (198,199,190), 24, 8, steps=28, opacity=0.78)
# feathers only on the body, in the direction of the form
for i in range(700):
    p = sample_poly(body_poly)
    add("heron_feathers", [(p[0],p[1]),(p[0]+rng.uniform(-10,10),p[1]+rng.uniform(4,18))],
        rng.uniform(1.2,3.6), mix((146,150,142),(210,210,196),rng.random()), 0.3, "dry")

# reflections: compressed, broken, lower contrast than the bird
for i in range(900):
    py = rng.uniform(HORIZON+20, H-10); x = HERON_X + rng.uniform(-80,80)
    ln = rng.uniform(8,55)
    add("reflection", [(x-ln/2,py),(x+ln/2,py+rng.uniform(-2,2))],
        rng.uniform(1.5,5), mix((130,142,132), water_color(x,py), 0.45), 0.10, "round_soft")
