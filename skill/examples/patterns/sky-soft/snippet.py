# sky-soft: vertical gradient + soft cloud bands
stops = [0.00, 0.16, 0.32, 0.48, 0.64, 0.78, 0.89, 1.00]
cols  = [[11,15,36],[22,28,60],[43,42,84],[75,54,99],
         [120,68,108],[179,94,112],[231,137,113],[255,190,136]]
for k in range(3):
    sky[..., k] = np.interp(yy/HOR, stops, [c[k] for c in cols])

def cloud_band(c0, c1, width, phase, noise, cut):
    center = c0 + (c1-c0)*u + 0.020*np.sin(2*np.pi*(u*1.35+phase)) + 0.025*(noise-.5)
    cover  = np.exp(-((sv-center)/width)**2 * 1.55)
    rag    = smoothstep(cut, cut+0.24, 0.70*noise + 0.30*n_hi)
    return cover*rag

# draw multiple bands, low opacity, warm only on lit underside
