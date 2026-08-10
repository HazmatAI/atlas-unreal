"""Route between two points using the pack's baked navigation grid.

Standalone (numpy only, no bpy) so it runs inside Blender or from a plain interpreter:

    from nav_route import NavGrid
    nav = NavGrid(pack_dir)
    pts = nav.route((x0, y0, z0), (x1, y1, z1))      # PACK space, Y up

WHY THIS EXISTS. Patrol waypoints are a NETWORK, not an ordered path: consecutive entries are not
guaranteed to be mutually visible, and walking a straight line between them sends a character
through tankers, containers and walls. The grid is the same one the viewer routes on, so a walk
built from it goes where a bot could actually go.

FORMAT (docs/extraction/colliders-interactables-and-semantics.md and nav.json). A layered 2.5-D
grid over the map's XZ, `res` metres per cell, `K` height layers per cell:

    nav.json         min_x, min_z, res, nx, nz, n_layers K, miss, climb, drop_max, vault, step_up,
                     slope_max_deg
    nav.bin          f32[nx*nz*K] LE, cell (iz*nx+ix) layer l at (iz*nx+ix)*K + l, heights
                     ASCENDING, `miss` (large negative) padding the unused layers
    nav_door.bin     u8[nx*nz]   1 = door cell, forced passable
    nav_blk.bin      u8[nx*nz*K] 8-dir edge mask, bit d = edge to NB[d] blocked by a thin wall
    nav_wallcell.bin u8[nx*nz]   1 = a wall occupies this cell's body column

THE RULES BELOW ARE A PORT, NOT AN APPROXIMATION. Every constant and branch mirrors the viewer's
router; a divergence here does not fail loudly, it produces a route through a wall. In particular:

  * a DOWN step is bounded exactly like an UP step (`drop_max`, run*slope_tan, capped by `vault`).
    A free-fall allowance is what lets a route leave the ground and traverse the top of a vehicle.
  * a DIAGONAL needs BOTH shared orthogonal sides floored, walkable and unblocked. A capsule
    cannot squeeze a corner where either side is a wall.
  * `near_wall` (any blocked edge, dilated one cell) adds a small per-cell cost so routes stand off
    walls by roughly the agent radius. It is a soft penalty and never closes a corridor.
  * a door on EITHER side forces a seam passable, and that check comes BEFORE the block mask so a
    stray bit cannot seal a doorway.
"""

import heapq
import json
import math
import os

import numpy as np

__all__ = ["NavGrid"]

NB = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
ORTHO_OF_DIAG = {4: (0, 2), 5: (0, 3), 6: (1, 2), 7: (1, 3)}
VERT = 6.0                 # vertical cost weight: climbing is dearer than walking
WALL_CLEARANCE = 0.35      # extra cost (x res) for a cell adjacent to a wall
START_SNAP_M = 16.0
DEST_SNAP_M = 12.0


class NavGrid(object):
    def __init__(self, pack_dir, verbose=True):
        d = os.path.abspath(pack_dir)
        meta = json.load(open(os.path.join(d, "nav.json"), encoding="utf-8"))
        self.nx = int(meta["nx"]); self.nz = int(meta["nz"]); self.k = int(meta["n_layers"])
        self.res = float(meta["res"])
        self.min_x = float(meta["min_x"]); self.min_z = float(meta["min_z"])
        self.miss = float(meta["miss"])
        self.climb = float(meta["climb"])
        self.drop_max = float(meta["drop_max"])
        self.vault = float(meta["vault"])
        self.step_up = float(meta["step_up"])
        slope = float(meta.get("slope_max_deg", meta.get("walk_slope_deg", 48.0)))
        self.slope_tan = math.tan(math.radians(min(max(slope, 20.0), 70.0)))

        n = self.nx * self.nz
        m = n * self.k
        self.h = np.fromfile(os.path.join(d, "nav.bin"), np.float32, m)
        if self.h.size != m:
            raise IOError("nav.bin is %d floats, expected %d" % (self.h.size, m))

        def _u8(name, count):
            p = os.path.join(d, name)
            if not os.path.exists(p):
                return np.zeros(count, np.uint8)
            a = np.fromfile(p, np.uint8, count)
            return a if a.size == count else np.zeros(count, np.uint8)

        self.door = _u8("nav_door.bin", n)
        self.blk = _u8("nav_blk.bin", m)
        self.wall_cell = _u8("nav_wallcell.bin", n)

        # near_wall: any blocked edge in the cell, then dilated one cell (the grid's coarse stand-in
        # for eroding the walkable area by the agent radius).
        seed = (self.blk.reshape(self.nz, self.nx, self.k) != 0).any(axis=2)
        dil = seed.copy()
        for dz in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dz == 0 and dx == 0:
                    continue
                dil |= np.roll(np.roll(seed, dz, axis=0), dx, axis=1)
        self.near_wall = dil.ravel()

        self._miss_half = self.miss * 0.5
        if verbose:
            print("[nav] grid %dx%dx%d @ %.2fm  step_up %.2f  vault %.2f  drop %.2f  slope %.0fdeg"
                  % (self.nx, self.nz, self.k, self.res, self.step_up, self.vault,
                     self.drop_max, slope))

    # ---- primitives -----------------------------------------------------------------------
    def h_lay(self, c, l):
        return float(self.h[c * self.k + l])

    def best_layer(self, c, ref_y):
        """Layer in cell `c` whose height is nearest `ref_y`, or -1. Layers ascend; miss trails."""
        base = c * self.k
        b, bd = -1, float("inf")
        for l in range(self.k):
            hh = float(self.h[base + l])
            if hh <= self._miss_half:
                break
            dd = abs(hh - ref_y)
            if dd < bd:
                bd, b = dd, l
        return b

    def walkable_step(self, up, run, forced):
        if forced:
            return (0.0 <= up <= self.vault) or (up < 0.0 and -up <= self.drop_max)
        if up > 0.0:
            return up <= self.vault and (up <= self.step_up
                                         or (up <= self.climb and up <= run * self.slope_tan))
        return -up <= min(max(self.drop_max, run * self.slope_tan), self.vault)

    def _ortho_ok(self, ix, iz, h_ref, blk_c, o):
        if (blk_c >> o) & 1:
            return False
        dx, dz = NB[o]
        jx, jz = ix + dx, iz + dz
        if jx < 0 or jz < 0 or jx >= self.nx or jz >= self.nz:
            return False
        oc = jz * self.nx + jx
        nl = self.best_layer(oc, h_ref)
        if nl < 0:
            return False
        run = math.sqrt(dx * dx + dz * dz) * self.res
        return self.walkable_step(self.h_lay(oc, nl) - h_ref, run, False)

    def _diag_ok(self, ix, iz, h_ref, blk_c, d):
        o1, o2 = ORTHO_OF_DIAG[d]
        return (self._ortho_ok(ix, iz, h_ref, blk_c, o1)
                and self._ortho_ok(ix, iz, h_ref, blk_c, o2))

    def node_pos(self, node):
        c, l = divmod(node, self.k)
        return (self.min_x + (c % self.nx) * self.res,
                self.h_lay(c, l),
                self.min_z + (c // self.nx) * self.res)

    def snap(self, x, y, z, reach_m):
        """Nearest cell+layer with a floor near y, spiralling out. None if nothing is in reach."""
        cx = int(round((x - self.min_x) / self.res))
        cz = int(round((z - self.min_z) / self.res))
        rings = int(math.ceil(reach_m / self.res))
        best, bscore = None, float("inf")
        for r in range(rings + 1):
            if best is not None:
                break                        # nearest ring wins; only tie-break within it
            for jz in range(cz - r, cz + r + 1):
                for jx in range(cx - r, cx + r + 1):
                    if r > 0 and abs(jx - cx) != r and abs(jz - cz) != r:
                        continue             # ring perimeter only
                    if jx < 0 or jz < 0 or jx >= self.nx or jz >= self.nz:
                        continue
                    c = jz * self.nx + jx
                    l = self.best_layer(c, y)
                    if l < 0:
                        continue
                    dy = abs(self.h_lay(c, l) - y)
                    if dy > 6.0:
                        continue
                    if dy < bscore:
                        bscore, best = dy, (c, l)
        return best

    # ---- A* -------------------------------------------------------------------------------
    def route(self, a, b, expand_cap=400000):
        """A* between two world points. Returns [(x, y, z), ...] in pack space, or None."""
        s = self.snap(a[0], a[1], a[2], START_SNAP_M)
        t = self.snap(b[0], b[1], b[2], DEST_SNAP_M)
        if s is None or t is None:
            return None
        k, nx, nz, res = self.k, self.nx, self.nz, self.res
        start = s[0] * k + s[1]
        goal = t[0] * k + t[1]
        dix, diz = t[0] % nx, t[0] // nx

        def heur(c):
            ix, iz = c % nx, c // nx
            return math.hypot(ix - dix, iz - diz) * res

        g = {start: 0.0}
        came = {}
        closed = set()
        heap = [(heur(s[0]), start)]
        expanded = 0
        while heap:
            _f, cur = heapq.heappop(heap)
            if cur in closed:
                continue
            if cur == goal:
                break
            closed.add(cur)
            expanded += 1
            if expanded > expand_cap:
                return None
            c, l = divmod(cur, k)
            ix, iz = c % nx, c // nx
            h_cur = self.h_lay(c, l)
            blk_c = int(self.blk[cur])
            gc = g[cur]
            for d in range(8):
                dx, dz = NB[d]
                jx, jz = ix + dx, iz + dz
                if jx < 0 or jz < 0 or jx >= nx or jz >= nz:
                    continue
                nc = jz * nx + jx
                nl = self.best_layer(nc, h_cur)
                if nl < 0:
                    continue
                forced = self.door[c] == 1 or self.door[nc] == 1
                if not forced and (blk_c >> d) & 1:
                    continue
                up = self.h_lay(nc, nl) - h_cur
                horiz = math.sqrt(dx * dx + dz * dz) * res
                if not self.walkable_step(up, horiz, forced):
                    continue
                if dx != 0 and dz != 0 and not forced \
                        and not self._diag_ok(ix, iz, h_cur, blk_c, d):
                    continue
                nn = nc * k + nl
                if nn in closed:
                    continue
                step = math.sqrt(horiz * horiz + (up * VERT) * (up * VERT))
                if self.near_wall[nc]:
                    step += res * WALL_CLEARANCE
                ng = gc + step
                if nn not in g or ng < g[nn]:
                    g[nn] = ng
                    came[nn] = cur
                    heapq.heappush(heap, (ng + heur(nc), nn))
        if goal not in g:
            return None
        out, n = [], goal
        while True:
            out.append(self.node_pos(n))
            if n == start:
                break
            n = came[n]
        out.reverse()
        return out

    def route_through(self, points, verbose=True):
        """Chain A* through a list of waypoints, dropping any leg that will not route."""
        full = []
        for i in range(len(points) - 1):
            leg = self.route(points[i], points[i + 1])
            if leg is None:
                if verbose:
                    print("[nav] leg %d->%d unroutable, skipped" % (i, i + 1))
                continue
            if full and leg:
                leg = leg[1:]
            full.extend(leg)
        if verbose and full:
            d = sum(math.dist(full[i], full[i + 1]) for i in range(len(full) - 1))
            print("[nav] route %d node(s), %.1f m" % (len(full), d))
        return full
