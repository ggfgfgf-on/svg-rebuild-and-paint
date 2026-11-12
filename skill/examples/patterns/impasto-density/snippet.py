# impasto-density: 3 passes, shrinking size, low opacity
for p in range(3):
    step = 11 + p*4
    for y in range(48, 590, step):
        size = rng.uniform(16,31) if p==0 else rng.uniform(8,17) if p==1 else rng.uniform(4,9)
        op   = rng.uniform(.72,.93) if p==0 else rng.uniform(.62,.88) if p==1 else rng.uniform(.45,.72)
        preset = rng.choice(["sponge","round_hard","bristle"]) if p==0 else                  "bristle" if p==1 else rng.choice(["dry","bristle","chalk"])
        ang = math.atan2(gy[y,x], gx[y,x]) + math.pi/2 + rng.uniform(-.22,.22)
        add("sky", curved(x,y,rng.uniform(13,44)*(1.2 if p==0 else .8),ang),
            size, color, op, preset, rng.uniform(.4,.8))

# budget: keep density on subject/rock; do not give sky the same density
