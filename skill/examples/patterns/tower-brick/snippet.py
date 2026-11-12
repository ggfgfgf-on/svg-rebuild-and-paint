# tower-brick: opaque body -> lit/shadow faces -> vertical knife -> windows
shape_ops.fill_polygon(doc, "tower", body, color=(150,111,91),
    color2=(118,89,87), size=14, opacity=1.0,
    preset="round_hard", spacing=5.5, jitter=0.10, edge=False)
shape_ops.fill_polygon(doc, "tower", lit_face, color=(222,172,119),
    size=3.6, opacity=0.58, preset="round_soft", spacing=3.4, edge=False)
shape_ops.fill_polygon(doc, "tower", right_face, color=(74,60,72),
    size=3.6, opacity=0.56, preset="round_soft", spacing=3.4, edge=False)

for i in range(380):  # vertical knife, not random cross-hatch
    y = r.uniform(252,690)
    x = r.uniform(tower_left(y)+5, tower_right(y)-5)
    t = (x-tower_left(y))/max(1.0,tower_right(y)-tower_left(y))
    c = mix((224,175,121),(83,66,75), t*0.95)
    add("tower", [(x,y),(x+r.uniform(-3,3), y-r.uniform(16,64))],
        r.uniform(5,13), c, r.uniform(0.18,0.42), "bristle")

# windows: separate layer, dark cavity + pale frame; do not paint into brick pass
