"""A follow camera solved as one continuous move, not frame by frame.

Standalone. Run inside Blender, then call ``solve_follow_camera(scene, arm, f0, f1)``.

WHY A SOLVER. A follow camera has to satisfy two goals that fight each other: keep the subject
unobstructed, and move smoothly. Deciding each frame independently and smoothing afterwards
satisfies neither - the smoothing drags the camera back through the wall the per-frame choice just
escaped. Because a render is offline we can do what a real camera department does and plan the
WHOLE move before shooting it: score every candidate position at every moment, then choose the
single sequence of positions with the lowest total cost, where cost includes how far the camera
moved between frames. That is a shortest-path problem over time, and Viterbi solves it exactly.

The three things the old fixed-offset rig got wrong, and what replaces them:

  * ONE RAY. Testing the line from the lens to the subject's chest says nothing about a lamp post
    covering his torso, or a chair covering his legs. Visibility here is the fraction of a set of
    BODY POINTS (head, chest, shoulders, hips, knees) with a clear line, so a partial block costs
    partially and the solver routes around it.
  * ONE SIDE. A fixed left offset has nowhere to go when the route hugs a building. Candidates
    cover the full circle, so the camera takes whichever side is open; the transition cost is what
    stops it flip-flopping, and it will only cross behind the subject when the far side stays
    better for long enough to be worth the move.
  * LOCAL SMOOTHING. A rate limit fights the placement instead of informing it. Here movement is
    part of the cost being minimised, so the solved path is already smooth and the filter afterwards
    only polishes it.

GRASS IS A SOFT OCCLUDER. Blades are thin and read as natural foreground, so they add cost rather
than disqualifying a position - otherwise the solver flees any spot with a tuft in front of it. The
subject's own body and rifle, and the atmosphere volume, are ignored entirely.

Raycasts run against ONE frame's geometry. Everything except the subject is static, and the subject
is on the ignore list, so re-evaluating the depsgraph per frame would cost time and change nothing.
"""

import math

import bpy
import mathutils

__all__ = ["solve_follow_camera", "cinematic_render_settings"]

# candidate grid: azimuth measured from the subject's forward, positive toward his left
AZIMUTHS = [math.radians(a) for a in range(-180, 180, 20)]
DISTANCES = [3.0, 4.0, 5.0, 6.0]
RISES = [1.35, 1.80, 2.25]

PREF_AZ = math.radians(118.0)   # behind and to the left: a 3/4 rear that still shows his face-side
IDEAL_DIST = 5.0
IDEAL_RISE = 1.80

W_BLOCKED = 60.0                # per unit of lost visibility - dominates everything else
W_GRASS = 3.0
W_DIST = 0.8
W_RISE = 0.6
W_AZ = 1.6
W_MOVE = 1.2                    # per square metre of camera travel between solve steps
W_GROUND = 40.0

BODY = [(0.30, 0.00), (0.90, 0.00), (1.35, 0.00),
        (1.35, -0.28), (1.35, 0.28), (1.68, 0.00)]   # (height, lateral offset) in metres

MIN_GROUND_CLEAR = 0.45
LEAD = 0.85                     # aim this far ahead of the subject, so he has looking room


def _fcurves(action):
    """Blender 4.4+ keeps an action's curves in a slot channelbag; `Action.fcurves` is gone in 5.x."""
    try:
        fcs = action.fcurves
        if fcs is not None:
            return list(fcs)
    except (AttributeError, TypeError):
        pass
    out = []
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for cb in getattr(strip, "channelbags", []):
                out.extend(cb.fcurves)
    return out


def _binomial(seq, passes=2):
    """Smooth a list of Vectors with a [1 2 1] kernel, endpoints held."""
    out = list(seq)
    for _ in range(passes):
        prev = list(out)
        for i in range(1, len(out) - 1):
            out[i] = (prev[i - 1] + prev[i] * 2.0 + prev[i + 1]) * 0.25
    return out


class _Los(object):
    """Line-of-sight against the static scene, with a per-object ignore set."""

    def __init__(self, scene, arm):
        self.scene = scene
        self.dg = bpy.context.evaluated_depsgraph_get()
        skip = set()
        for o in bpy.data.objects:
            n = o.name
            if n.startswith(("grass_kind", "eft_atmosphere", "track", "focus", "shot")):
                continue                       # grass is handled separately, the rest are ignored
            a = o
            while a is not None:
                if a is arm:
                    skip.add(n)
                    break
                a = a.parent
        self.skip = skip

    def clear(self, a, b):
        """(visible, grass_hits) for the segment a->b."""
        d = b - a
        remaining = d.length
        if remaining < 1e-5:
            return True, 0
        d = d / remaining
        org, grass = a.copy(), 0
        for _ in range(16):
            hit, loc, _n, _i, obj, _m = self.scene.ray_cast(self.dg, org, d, distance=remaining)
            if not hit:
                return True, grass
            name = obj.name
            if name.startswith("grass_kind"):
                grass += 1
            elif not (name in self.skip
                      or name.startswith(("eft_atmosphere", "track", "focus", "shot"))):
                return False, grass
            remaining -= (loc - org).length + 1e-3
            if remaining <= 1e-3:
                return True, grass
            org = loc + d * 1e-3
        return True, grass

    def ground_clear(self, p):
        """Height of `p` above whatever is under it (large if it is over a void)."""
        hit, loc, _n, _i, _o, _m = self.scene.ray_cast(
            self.dg, p, mathutils.Vector((0, 0, -1)), distance=12.0)
        return (p.z - loc.z) if hit else 99.0


def solve_follow_camera(scene, arm, f0, f1, step=3, lens=50.0, fstop=2.8, blades=0,
                        verbose=True):
    """Create and key a follow camera for [f0, f1]. Returns the camera object.

    `blades` is the iris blade count. 0 is Blender's default and means a mathematically perfect
    circular aperture, which no real lens has: an out-of-focus highlight from a stopped-down 50 mm
    is a polygon with the blade count's number of sides. Free (measured 10.56 s at 0 against
    10.72/10.45 s at 9 on the real scene, i.e. inside run-to-run noise), and a departure from the
    viewer only in the sense that DOF already is - the viewer has no defocus at all.
    """
    import numpy as np

    # ---- 1. record the subject's motion, then stop touching the timeline --------------------
    frames = list(range(f0, f1 + 1))
    pos, fwd = [], []
    for fr in frames:
        scene.frame_set(fr)
        bpy.context.view_layer.update()
        pos.append(arm.matrix_world.translation.copy())
        f = (arm.matrix_world.to_3x3() @ mathutils.Vector((0, 0, 1)))
        f.z = 0.0
        fwd.append(f.normalized() if f.length > 1e-5 else mathutils.Vector((1, 0, 0)))
    # the walk cycle yaws the root slightly every step; the camera should not answer that
    fwd = [v.normalized() for v in _binomial(fwd, passes=6)]
    pos_s = _binomial(pos, passes=2)

    los = _Los(scene, arm)
    states = [(a, d, r) for a in AZIMUTHS for d in DISTANCES for r in RISES]
    ns = len(states)

    def offset(st, i):
        a, d, r = st
        left = mathutils.Vector((-fwd[i].y, fwd[i].x, 0.0))
        return fwd[i] * (math.cos(a) * d) + left * (math.sin(a) * d) + mathutils.Vector((0, 0, r))

    # static per-state framing cost
    base = np.empty(ns, np.float32)
    for j, (a, d, r) in enumerate(states):
        da = abs((a - PREF_AZ + math.pi) % (2 * math.pi) - math.pi)
        base[j] = (W_AZ * (da / math.radians(30.0))
                   + W_DIST * abs(d - IDEAL_DIST) + W_RISE * abs(r - IDEAL_RISE))

    # ---- 2. emission cost at every solve step -----------------------------------------------
    solve_idx = list(range(0, len(frames), step))
    if solve_idx[-1] != len(frames) - 1:
        solve_idx.append(len(frames) - 1)
    emis = np.empty((len(solve_idx), ns), np.float32)
    for t, i in enumerate(solve_idx):
        p, f = pos_s[i], fwd[i]
        left = mathutils.Vector((-f.y, f.x, 0.0))
        pts = [p + mathutils.Vector((0, 0, h)) + left * lat for h, lat in BODY]
        for j, st in enumerate(states):
            cam = p + offset(st, i)
            vis, grass = 0, 0
            for k, bp in enumerate(pts):
                ok, g = los.clear(bp, cam)
                vis += 1 if ok else 0
                grass += g
                if k == 1 and vis == 0:
                    break                      # chest blocked: cheap reject, do not ray the rest
            frac = vis / float(len(pts))
            c = base[j] + W_BLOCKED * (1.0 - frac) + W_GRASS * min(grass, 4) / 4.0
            gc = los.ground_clear(cam)
            if gc < MIN_GROUND_CLEAR:
                c += W_GROUND * (MIN_GROUND_CLEAR - gc + 0.1)
            emis[t, j] = c

    # ---- 3. transition cost, and the Viterbi pass -------------------------------------------
    # Movement is measured in the subject's frame, which is what keeps the camera steady relative
    # to him rather than steady in the world (a world-steady camera falls behind a walking subject).
    ox = np.array([[math.cos(a) * d, math.sin(a) * d, r] for a, d, r in states], np.float32)
    diff = ox[:, None, :] - ox[None, :, :]
    trans = W_MOVE * np.einsum("ijk,ijk->ij", diff, diff) / max(step, 1)

    dp = emis[0].copy()
    back = np.zeros((len(solve_idx), ns), np.int32)
    for t in range(1, len(solve_idx)):
        tot = dp[:, None] + trans
        back[t] = np.argmin(tot, axis=0)
        dp = tot[back[t], np.arange(ns)] + emis[t]
    chain = [int(np.argmin(dp))]
    for t in range(len(solve_idx) - 1, 0, -1):
        chain.append(int(back[t, chain[-1]]))
    chain.reverse()

    # ---- 4. to world space, smooth, repair --------------------------------------------------
    key_pos = [pos_s[i] + offset(states[chain[t]], i) for t, i in enumerate(solve_idx)]
    key_pos = _binomial(key_pos, passes=3)

    # Smoothing is free to cut a corner into geometry. Pull any offending key back along its own
    # view ray until it is clear, which preserves the direction the move was heading.
    repaired = 0
    for t, i in enumerate(solve_idx):
        eye = pos_s[i] + mathutils.Vector((0, 0, 1.35))
        v = key_pos[t] - eye
        L = v.length
        if L < 1e-4:
            continue
        v = v / L
        ok, _g = los.clear(eye, key_pos[t])
        if ok:
            continue
        lo, hi = 1.2, L
        for _ in range(6):                     # bisect for the furthest clear point
            mid = 0.5 * (lo + hi)
            if los.clear(eye, eye + v * mid)[0]:
                lo = mid
            else:
                hi = mid
        key_pos[t] = eye + v * lo
        repaired += 1
    key_pos = _binomial(key_pos, passes=1)

    # resample the solved keys back to every frame
    cam_at = []
    for n in range(len(frames)):
        t = min(n / float(step), len(key_pos) - 1.0)
        lo = int(math.floor(t)); hi = min(lo + 1, len(key_pos) - 1)
        cam_at.append(key_pos[lo].lerp(key_pos[hi], t - lo))

    # ---- 5. build the camera ----------------------------------------------------------------
    for n in ("shot", "track", "focus"):
        o = bpy.data.objects.get(n)
        if o:
            bpy.data.objects.remove(o, do_unlink=True)
    cd = bpy.data.cameras.new("shot")
    cd.lens = lens; cd.clip_start = 0.05; cd.clip_end = 4000.0
    cd.dof.use_dof = True; cd.dof.aperture_fstop = fstop
    cd.dof.aperture_blades = int(blades)         # 0 = perfect circle; see the docstring
    cam = bpy.data.objects.new("shot", cd); scene.collection.objects.link(cam)
    cam.rotation_mode = 'QUATERNION'
    focus = bpy.data.objects.new("focus", None); scene.collection.objects.link(focus)
    focus.empty_display_size = 0.2
    cd.dof.focus_object = focus

    # aim ahead of him so he sits on the trailing side of frame with room to walk into
    aim = _binomial([pos_s[i] + mathutils.Vector((0, 0, 1.30)) + fwd[i] * LEAD
                     for i in range(len(frames))], passes=4)
    blocked = 0
    for n, fr in enumerate(frames):
        cam.location = cam_at[n]
        cam.rotation_quaternion = (aim[n] - cam_at[n]).to_track_quat('-Z', 'Y')
        cam.keyframe_insert("location", frame=fr)
        cam.keyframe_insert("rotation_quaternion", frame=fr)
        focus.location = pos_s[n] + mathutils.Vector((0, 0, 1.30))
        focus.keyframe_insert("location", frame=fr)
        if not los.clear(pos_s[n] + mathutils.Vector((0, 0, 1.35)), cam_at[n])[0]:
            blocked += 1
    # Every frame is keyed, so the curve should pass straight through the solved points. Auto-Bezier
    # handles overshoot on a direction change, and an overshoot here pokes the lens into a wall.
    for fc in _fcurves(cam.animation_data.action):
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
    scene.camera = cam

    if verbose:
        dists = [(cam_at[n] - pos_s[n]).length for n in range(len(frames))]
        print("[cine] %d frames, %d solve steps, %d state(s) per step"
              % (len(frames), len(solve_idx), ns))
        print("[cine] boom %.2f..%.2f m (ideal %.1f); %d key(s) repaired after smoothing"
              % (min(dists), max(dists), IDEAL_DIST, repaired))
        print("[cine] chest occluded on %d/%d frames (%.1f%%)"
              % (blocked, len(frames), 100.0 * blocked / len(frames)))
    return cam


def cinematic_render_settings(scene, samples=256, res=(2560, 1440), threshold=0.005):
    """Everything that buys realism per unit of render time, in roughly that order.

    MOTION BLUR at a 180-degree shutter (`shutter = 0.5`) is the single biggest one: it is what a
    real camera does, and without it a walk cycle reads as stop-motion no matter how many samples
    the frame gets.

    TRANSPARENT BOUNCES matter far more here than the headline `max_bounces`, and the failure is
    not "a bit dark" - it is missing pixels. The counter is per PATH, not per segment: the camera
    ray, every diffuse bounce after it and the shadow ray toward the sun all spend from one budget.
    Every leaf, blade and chain-link is alpha-tested, so a ray crossing foliage exhausts it on
    transparency alone, and Cycles then fails CLOSED - a camera ray that runs out is terminated
    black, a shadow ray that runs out is reported fully occluded. Measured on example_scene's grass
    field: the default 8 leaves 14.78% of the frame at EXACTLY 0.0 luma with the mean 12.2% low;
    32 (what this function used to set, and what the line below used to claim was enough) still
    leaves 0.48% black; 256 is bit-identical to the 1024 maximum for +0.3 s on a 2.0 s frame. So
    256 it is - a transparent bounce does no shading, and there is nothing to buy by shaving it.

    NOTE: nothing in the repo calls this function (it is exported in __all__ and has no caller),
    so these settings do NOT reach the shipped scene. example_scene.py sets its own render config
    in step 8, and that is where the fix above has to live to have any effect.

    A physically sized SUN (0.526 degrees, the real angular diameter) fixes shadow penumbra width,
    which the eye reads as "outdoors" without being able to say why. The stock 1-2 degrees quietly
    softens every contact shadow.

    ADAPTIVE SAMPLING makes the sample count much cheaper than it looks: lowering the noise
    threshold concentrates samples on the noisy regions instead of re-rendering clean sky.
    """
    scene.render.resolution_x, scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.use_motion_blur = True
    scene.render.motion_blur_shutter = 0.5
    scene.render.filter_size = 1.20              # stock 1.5 is soft at high resolution
    # Only the character moves, so re-exporting the map every frame is wasted work. Measured on
    # this scene: per-frame sync 2.4-2.9 s without, 0.6-1.2 s with. Worth about 5% of a 1440p
    # frame, and much more on lighter shots where path tracing is not the bottleneck.
    scene.render.use_persistent_data = True

    c = scene.cycles
    c.samples = samples
    c.use_adaptive_sampling = True
    c.adaptive_threshold = threshold
    c.use_denoising = True
    c.max_bounces = 16
    c.diffuse_bounces = 8
    c.glossy_bounces = 8
    c.transmission_bounces = 16
    c.transparent_max_bounces = 256              # alpha-tested foliage, see above
    c.volume_bounces = 2
    for attr, val in (("denoiser", 'OPENIMAGEDENOISE'), ("denoising_prefilter", 'ACCURATE'),
                      ("denoising_quality", 'HIGH'), ("denoising_use_gpu", True),
                      ("use_light_tree", True)):
        try:
            setattr(c, attr, val)
        except Exception:
            pass

    for lt in bpy.data.lights:
        if lt.type == 'SUN':
            lt.angle = math.radians(0.526)       # the sun's real angular diameter

    print("[cine] %dx%d, %d samples (threshold %.3f), motion blur 180 deg, "
          "transparent bounces %d, sun 0.526 deg"
          % (res[0], res[1], samples, threshold, c.transparent_max_bounces))
