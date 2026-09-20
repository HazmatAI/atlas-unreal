#!/usr/bin/env python3
"""Find spatially adjacent active/LOD0 geometry roots from an Atlas .eftpack.

Bounds are obtained from each mesh's local vertex AABB transformed by the
instance's full affine matrix (all 8 corners). Distances are invariant under
Atlas-to-Unreal axis permutation; reported coordinates are Atlas metres.
"""
import argparse
import bisect
import heapq
import json
import math
import re
import pathlib
import struct


def read_instances(manifest, instance_bytes):
    layout = manifest['instance']
    assert len(instance_bytes) == manifest['instanceCount'] * layout['stride']
    fmt_codes = {'f32x12': '12f', 'u32': 'I', 'i32': 'i'}
    records = []
    for index in range(manifest['instanceCount']):
        base = index * layout['stride']
        row = {
            field['name']: struct.unpack_from('<' + fmt_codes[field['fmt']], instance_bytes,
                                                base + field['offset'])
            for field in layout['fields']
        }
        row = {key: (value[0] if len(value) == 1 else value) for key, value in row.items()}
        row['index'] = index
        records.append(row)
    return records


def mesh_bounds(manifest, mesh_bytes):
    stride = manifest['vertex']['stride']
    result = {}
    for mesh in manifest['meshes']:
        lows = [math.inf] * 3
        highs = [-math.inf] * 3
        for i in range(mesh['vtxCount']):
            point = struct.unpack_from('<3f', mesh_bytes, mesh['vtxOffset'] + i * stride)
            for axis in range(3):
                lows[axis] = min(lows[axis], point[axis])
                highs[axis] = max(highs[axis], point[axis])
        result[mesh['id']] = (lows, highs)
    return result


def transform_bounds(local, affine):
    lows, highs = local
    world_lows = [math.inf] * 3
    world_highs = [-math.inf] * 3
    for mask in range(8):
        p = [highs[a] if mask & (1 << a) else lows[a] for a in range(3)] + [1.0]
        for axis in range(3):
            value = sum(p[k] * affine[axis * 4 + k] for k in range(4))
            world_lows[axis] = min(world_lows[axis], value)
            world_highs[axis] = max(world_highs[axis], value)
    return (world_lows, world_highs)


def bounds_union(rows):
    if not rows:
        return None
    return ([min(row['bounds'][0][a] for row in rows) for a in range(3)],
            [max(row['bounds'][1][a] for row in rows) for a in range(3)])


def bounds_distance(a, b):
    delta = [max(0.0, a[0][i] - b[1][i], b[0][i] - a[1][i]) for i in range(3)]
    return delta, math.sqrt(sum(v * v for v in delta))


class Node:
    __slots__ = ('bounds', 'items', 'left', 'right', 'size')

    def __init__(self, rows, leaf_size=8):
        self.bounds = bounds_union(rows)
        self.size = len(rows)
        self.items = rows if len(rows) <= leaf_size else None
        self.left = self.right = None
        if self.items is None:
            extents = [self.bounds[1][a] - self.bounds[0][a] for a in range(3)]
            axis = max(range(3), key=extents.__getitem__)
            rows.sort(key=lambda row: (row['bounds'][0][axis] + row['bounds'][1][axis]) * 0.5)
            middle = len(rows) // 2
            self.left = Node(rows[:middle], leaf_size)
            self.right = Node(rows[middle:], leaf_size)


def nearest_pair(root_a, root_b):
    a_tree, b_tree = Node(list(root_a)), Node(list(root_b))
    queue = []
    serial = 0
    initial = bounds_distance(a_tree.bounds, b_tree.bounds)[1]
    heapq.heappush(queue, (initial, serial, a_tree, b_tree))
    best = math.inf
    pair = None
    while queue:
        lower, _, a, b = heapq.heappop(queue)
        if lower > best:
            continue
        if a.items is not None and b.items is not None:
            for ra in a.items:
                for rb in b.items:
                    delta, distance = bounds_distance(ra['bounds'], rb['bounds'])
                    if distance < best:
                        best = distance
                        overlap = [max(0.0, min(ra['bounds'][1][i], rb['bounds'][1][i])
                                        - max(ra['bounds'][0][i], rb['bounds'][0][i])) for i in range(3)]
                        pair = (ra, rb, delta, overlap)
            if best == 0.0:
                # AABB distance cannot be lower than zero; one concrete intersect/touch pair is sufficient.
                break
            continue
        if b.items is None and (a.items is not None or b.size >= a.size):
            children = ((a, b.left), (a, b.right))
        else:
            children = ((a.left, b), (a.right, b))
        for ca, cb in children:
            d = bounds_distance(ca.bounds, cb.bounds)[1]
            if d <= best:
                serial += 1
                heapq.heappush(queue, (d, serial, ca, cb))
    return best, pair


def collect_near_pairs(root_a, root_b, threshold, cap=200000):
    """Count actual instance-AABB pairs within threshold and sample contact shapes."""
    a_tree, b_tree = Node(list(root_a)), Node(list(root_b))
    stack = [(a_tree, b_tree)]
    count = 0
    relation_counts = {'face_touch': 0, 'edge_or_corner_touch': 0, 'overlap': 0,
                       'near_gap': 0}
    best_contacts = []
    best_non_decal_contacts = []
    threshold_sq = threshold * threshold
    epsilon = 1e-5
    while stack:
        a, b = stack.pop()
        if bounds_distance(a.bounds, b.bounds)[1] > threshold:
            continue
        if a.items is not None and b.items is not None:
            for ra in a.items:
                for rb in b.items:
                    delta, distance = bounds_distance(ra['bounds'], rb['bounds'])
                    if distance * distance > threshold_sq:
                        continue
                    count += 1
                    overlap = [max(0.0, min(ra['bounds'][1][i], rb['bounds'][1][i])
                                    - max(ra['bounds'][0][i], rb['bounds'][0][i])) for i in range(3)]
                    if distance > epsilon:
                        relation = 'near_gap'
                    else:
                        positive = sum(v > epsilon for v in overlap)
                        relation = 'face_touch' if positive == 2 else ('edge_or_corner_touch' if positive <= 1 else 'overlap')
                    relation_counts[relation] += 1
                    # Rank by smallest gap, then by a face-like contact over volume overlap,
                    # with shallow overlap preferred to broad AABB penetration.
                    rank = (round(distance, 9), 0 if relation == 'face_touch' else 1,
                            sum(overlap), ra['index'], rb['index'])
                    entry = {'rank': rank, 'a': ra, 'b': rb, 'distance': distance,
                             'gapAxes': delta, 'overlapAxes': overlap, 'relation': relation}
                    _keep_best(best_contacts, entry, rank, count)
                    if not re.search(r'decal', ra['meshName'], re.IGNORECASE) \
                            and not re.search(r'decal', rb['meshName'], re.IGNORECASE):
                        _keep_best(best_non_decal_contacts, entry, rank, count)
                    if count >= cap:
                        return (count, relation_counts,
                                [e[2] for e in sorted(best_contacts, key=lambda x: x[2]['rank'])],
                                [e[2] for e in sorted(best_non_decal_contacts, key=lambda x: x[2]['rank'])], True)
            continue
        if b.items is None and (a.items is not None or b.size >= a.size):
            stack.extend(((a, b.left), (a, b.right)))
        else:
            stack.extend(((a.left, b), (a.right, b)))
    return (count, relation_counts,
            [e[2] for e in sorted(best_contacts, key=lambda x: x[2]['rank'])],
            [e[2] for e in sorted(best_non_decal_contacts, key=lambda x: x[2]['rank'])], False)


def _keep_best(heap, entry, rank, serial):
    if len(heap) < 20:
        heapq.heappush(heap, (_invert_rank(rank), serial, entry))
    elif rank < heap[0][2]['rank']:
        heapq.heapreplace(heap, (_invert_rank(rank), serial, entry))


def _invert_rank(rank):
    # Max-heap key for heapq, keeping the worst retained candidate at index zero.
    return tuple(-v if isinstance(v, (int, float)) else v for v in rank)


def compact_pair(pair, distance):
    a, b, delta, overlap = pair
    return {'distanceM': distance, 'firstInstanceId': a['index'], 'firstMeshId': a['meshId'],
            'secondInstanceId': b['index'], 'secondMeshId': b['meshId'], 'gapAxesM': delta,
            'overlapAxesM': overlap, 'firstBoundsM': a['bounds'], 'secondBoundsM': b['bounds']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', required=True, type=pathlib.Path)
    parser.add_argument('--target-root', type=int, default=3)
    parser.add_argument('--near-m', type=float, default=0.5)
    parser.add_argument('--output', type=pathlib.Path)
    args = parser.parse_args()
    pack = args.pack
    manifest = json.loads((pack / 'manifest.json').read_text(encoding='utf-8'))
    assert 0 <= args.target_root < len(manifest['roots'])
    instances = read_instances(manifest, (pack / 'instances.bin').read_bytes())
    local_bounds = mesh_bounds(manifest, (pack / 'meshes.bin').read_bytes())
    roots = {}
    mesh_names = {mesh['id']: mesh['name'] for mesh in manifest['meshes']}
    for row in instances:
        if row['flags'] & 8 or row['lodIndex'] > 0:
            continue
        roots.setdefault(row['rootId'], []).append({
            'index': row['index'], 'meshId': row['meshId'],
            'meshName': mesh_names[row['meshId']],
            'bounds': transform_bounds(local_bounds[row['meshId']], row['affine'])})
    target = roots[args.target_root]
    target_union = bounds_union(target)
    results = []
    for root_id, rows in roots.items():
        if root_id == args.target_root:
            continue
        union = bounds_union(rows)
        axis_gap, aggregate_distance = bounds_distance(target_union, union)
        pair_distance, pair = nearest_pair(target, rows)
        near_count, relations, contacts, non_decal_contacts, capped = collect_near_pairs(target, rows, args.near_m)
        results.append({
            'rootId': root_id, 'rootName': manifest['roots'][root_id],
            'activeLod0Instances': len(rows), 'uniqueMeshes': len({r['meshId'] for r in rows}),
            'boundsM': union, 'aggregateAabbDistanceM': aggregate_distance,
            'aggregateGapAxesM': axis_gap, 'nearestInstanceAabbDistanceM': pair_distance,
            'nearestPair': compact_pair(pair, pair_distance) if pair else None,
            'instanceAabbPairsWithinNearThreshold': near_count,
            'withinThresholdRelations': relations, 'nearPairEnumerationCapped': capped,
            'bestNearPairs': [{k: v for k, v in item.items() if k != 'rank'} for item in contacts],
            'bestNearPairsExcludingDecalMeshes': [{k: v for k, v in item.items() if k != 'rank'} for item in non_decal_contacts]})
    results.sort(key=lambda r: (r['nearestInstanceAabbDistanceM'], r['aggregateAabbDistanceM'], r['rootId']))
    output = {
        'sourceFingerprint': manifest.get('sourceFingerprint'), 'targetRootId': args.target_root,
        'targetRootName': manifest['roots'][args.target_root],
        'boundsMethod': 'mesh-local vertex AABB transformed by all 8 corners of instance affine; active and LodIndex<=0 only',
        'coordinates': 'Atlas metres; Euclidean distances are unchanged by Unreal axis permutation',
        'targetActiveLod0Instances': len(target), 'targetUniqueMeshes': len({r['meshId'] for r in target}),
        'targetBoundsM': target_union, 'nearThresholdM': args.near_m,
        'candidates': results}
    text = json.dumps(output, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
