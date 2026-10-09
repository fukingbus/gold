"""Export only requested chest maps from a verified WLRE packed release.

Requires the local wlre package (PYTHONPATH=<wlre>/src) and its dependencies.
Inputs are read-only. PNG textures are copied byte-for-byte; missing textures
use exact, catalog-bound Recast geometry, not inferred physical terrain.

Example:
  python scripts/export_maps.py --wlre-root <wlre> --pipeline <pipeline> \
    --navigation-source <umbra>/.tools/tracker-nav-source-1.3.19
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import sys


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def render_navigation(mesh: dict, scene: dict) -> tuple[bytes, dict, dict]:
    """A uniform, north-up XZ rendering; no fitting to markers or terrain art."""
    from PIL import Image, ImageDraw
    vertices = [v for g in mesh['graphs'] for v in g['world_vertices_int3']]
    require(bool(vertices), 'empty navigation geometry')
    x0, x1 = min(v[0] for v in vertices) / 1000, max(v[0] for v in vertices) / 1000
    z0, z1 = min(v[2] for v in vertices) / 1000, max(v[2] for v in vertices) / 1000
    rate = 920 / max(x1 - x0, z1 - z0)
    pad_x = (1024 - (x1 - x0) * rate) / 2
    pad_y = (1024 - (z1 - z0) * rate) / 2
    height = scene['map_extent']['map_height_raw']
    height = 6992 if height == 65535 else height
    projection = {'scale_x': rate / 100, 'scale_y': rate / 100,
                  'offset_x': pad_x - x0 * rate,
                  'offset_y': pad_y + (z1 - height / 100) * rate}
    canvas = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    colors = {0: '#277f74', 1: '#252d35', 2: '#1d2932', 3: '#329586', 4: '#30353d'}
    def project(v):
        return ((v[0] / 1000 - x0) * rate + pad_x,
                (z1 - v[2] / 1000) * rate + pad_y)
    triangles = []
    edges = Counter()
    edge_points = {}
    for graph_index, graph in enumerate(mesh['graphs']):
        for node in graph['nodes']:
            tag = node['tag']
            ids = node['vertices']
            points = [project(graph['world_vertices_int3'][i]) for i in ids]
            triangles.append((tag, points))
            for i in range(3):
                key = (graph_index, tag, *sorted((ids[i], ids[(i + 1) % 3])))
                edges[key] += 1
                edge_points[key] = (points[i], points[(i + 1) % 3])
    for tag, points in sorted(triangles, key=lambda x: x[0] in (0, 3)):
        draw.polygon(points, fill=colors.get(tag, '#292e36'))
    for key, count in edges.items():
        if count == 1:
            draw.line(edge_points[key], fill='#64c7b7' if key[1] in (0, 3) else '#48515a', width=2)
    output = io.BytesIO()
    canvas.save(output, format='PNG', optimize=False)
    return output.getvalue(), projection, {
        'world_bounds_xz': [x0, z0, x1, z1],
        'vertices': len(vertices), 'triangles': len(triangles),
        'tags': dict(sorted(Counter(tag for tag, _ in triangles).items())),
        'geometry_note': 'Static cached navigation areas; uncovered pixels are not verified physical obstacles.',
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wlre-root', type=Path, required=True)
    parser.add_argument('--pipeline', type=Path, required=True)
    parser.add_argument('--navigation-source', type=Path, required=True)
    parser.add_argument('--release-id', default='1.3.19_d6bc1e25_e7df651c')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--roster', type=Path)
    parser.add_argument('--lua-projection-evidence', type=Path,
                        help='Optional fresh hash-bound query from the WLRE Lua recovery helper')
    parser.add_argument('--scene-ids', help='Only for preliminary export before the audited roster exists')
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(args.wlre_root / 'src'))
    from PIL import Image, __version__ as pillow_version
    from wlre.asset_pipeline.packed_storage import AssetStore, PACK_INDEX_SUFFIX
    from wlre.asset_pipeline.packed_unity_extract import load_packed_unity_extraction_manifest
    from wlre.navigation.navmesh import parse_navmesh, world_to_minimap
    from wlre.navigation.scenes import parse_scenes, parse_text_table

    release_path = args.pipeline / 'releases' / args.release_id
    manifest_path = release_path / 'unity/web-runtime.packed-extraction.json'
    manifest_bytes = manifest_path.read_bytes()
    manifest = load_packed_unity_extraction_manifest(manifest_path)
    require(manifest.release_id == args.release_id, 'packed release mismatch')
    release_bytes = (release_path / 'release.json').read_bytes()
    release = json.loads(release_bytes)
    require(release['identity']['release_id'] == args.release_id, 'release identity mismatch')
    catalog_meta = next(x for x in release['metadata'] if x['logical_id'] == 'MainPackage.json')
    catalog_bytes = (args.pipeline / 'cache' / catalog_meta['cache_object']).read_bytes()
    require(sha(catalog_bytes) == catalog_meta['sha256'], 'main catalog mismatch')
    catalog = json.loads(catalog_bytes)
    catalog_bundles = {b['BundleName']: b for b in catalog['BundleList']}
    projection_evidence_path = args.output / 'evidence/map-projection.json'
    if args.lua_projection_evidence:
        raw = args.lua_projection_evidence.read_bytes()
        query = json.loads(raw)
        require(query['source_count'] == query['roundtrips_verified'], 'Lua recovery has unverified sources')
        wanted = {'Logic/Stage.lua': {66, 67, 68, 165, 167, 174, 176, 387},
                  'UI/Logic/UIMain.lua': {24, 34, *range(734, 757), 846, 847, 1220, 1233}}
        lines = {}
        for line in query['matches'] + query['snippets']:
            if line['path'] in wanted and line['line'] in wanted[line['path']]:
                lines[(line['path'], line['line'])] = {k: line[k] for k in ('path', 'line', 'text')}
        # The focused current query omits a few display lines. Reuse archived
        # snippets only after the complete decrypted file hashes match current.
        prior_bytes = (args.wlre_root / 'evidence/current/map-navigation-audit.json').read_bytes()
        prior = json.loads(prior_bytes)['lua']
        current_sources = {s['path']: s for s in query['sources']}
        prior_sources = {s['path']: s for s in prior['relevant_sources']}
        for path in wanted:
            require(current_sources[path]['decrypted_sha256'] == prior_sources[path]['decrypted_sha256'],
                    f'archived projection snippets differ from current Lua: {path}')
        for line in prior['matches'] + prior['snippets']:
            if line['path'] in wanted and line['line'] in wanted[line['path']]:
                key = (line['path'], line['line'])
                value = {k: line[k] for k in ('path', 'line', 'text')}
                require(key not in lines or lines[key] == value, 'same-hash Lua snippets disagree')
                lines.setdefault(key, value)
        require(all((path, number) in lines for path, numbers in wanted.items() for number in numbers),
                'projection evidence is missing required exact source lines')
        write_json(projection_evidence_path, {
            'schema_version': 1,
            'verified_date': '2026-10-09',
            'query_output_sha256': sha(raw),
            'archived_navigation_audit_sha256': sha(prior_bytes),
            'profile_id': query['profile_id'], 'profile_sha256': query['profile_sha256'],
            'client_identity': query['client_identity'],
            'source_count': query['source_count'], 'roundtrips_verified': query['roundtrips_verified'],
            'sources': [s for s in query['sources'] if s['path'] in wanted],
            'lines': [lines[k] for k in sorted(lines)],
            'formula': {'world_x': 'gameX / 100', 'world_z': '(mapHeight - gameY) / 100',
                        'rate_per_world_unit': '100000 / max(mapWidth, mapHeight)',
                        'image_x': 'worldX * rate + SmallMapOffsetX',
                        'image_y': '1024 - (worldZ * rate + SmallMapOffsetY)'},
            'resolution': 'The recovered client uses one max-axis scale for both axes. This supersedes the older independently stretched WebUI formula for this viewer. Map offsets are translations, not per-axis stretch factors.',
            'scope': 'Current installed Lua is hash-bound and byte-identical for these two files to the earlier navigation audit. Every image and ScenesData input separately binds to the requested managed release.'
        })
    require(projection_evidence_path.is_file(), 'hash-bound projection evidence is required')

    store_root = args.pipeline / 'asset-store'
    indexes = [store_root / 'indexes/sha256' / digest[:2] / f'{digest}{PACK_INDEX_SUFFIX}'
               for digest in manifest.pack_index_sha256]
    store = AssetStore(store_root, indexes)
    def exact(logical_name, type_name):
        found = [a for a in manifest.assets if a.logical_name == logical_name and a.type_name == type_name]
        require(len(found) == 1, f'expected one exact {type_name} {logical_name}: {len(found)}')
        return found[0]
    text_asset = exact('TextData_C', 'TextAsset')
    scenes_asset = exact('ScenesData_C', 'TextAsset')
    texts = parse_text_table(store.read(text_asset.ref), Path('TextData_C'))
    scenes = {s['scene_id']: s for s in parse_scenes(store.read(scenes_asset.ref), texts)}
    roster_path = args.roster or args.output / 'data/chests.json'
    roster = json.loads(roster_path.read_bytes()) if roster_path.exists() else None
    require(roster is not None or bool(args.scene_ids), 'audited chest roster is required')
    scene_ids = sorted(int(x) for x in args.scene_ids.split(',')) if args.scene_ids else sorted(m['id'] for m in roster['maps'])
    require(len(scene_ids) == len(set(scene_ids)), 'duplicate requested scene')
    inventory_bytes = (args.navigation_source / 'inventory.json').read_bytes()
    inventory = json.loads(inventory_bytes)
    require(inventory['catalog_sha256'] == catalog_meta['sha256'], 'navigation catalog is from another release')
    nav_models = {int(s['model']): s for s in inventory['scenes'] if s['model'].isdigit()}
    assets_dir = args.output / 'assets/maps'
    assets_dir.mkdir(parents=True, exist_ok=True)
    maps, audit_maps = [], []
    unique_files = {}
    for scene_id in scene_ids:
        scene = scenes[scene_id]
        model_id = scene['model_id']
        width = scene['map_extent']['map_width_raw']
        height = scene['map_extent']['map_height_raw']
        width, height = (6992 if width == 65535 else width), (6992 if height == 65535 else height)
        candidates = [a for a in manifest.assets if a.selector == 'minimaps' and a.type_name == 'Texture2D'
                      and a.logical_name == f'SmallMap2D_{model_id}']
        require(len(candidates) <= 1, f'ambiguous exact minimap {model_id}')
        evidence = {'scene_id': scene_id, 'model_id': model_id,
                    'scene': scene, 'image_name': f'SmallMap2D_{model_id}'}
        catalog_maps = [a for a in catalog['AssetList'] if a['Address'] == f'SmallMap2D_{model_id}']
        evidence['catalog_minimap_addresses'] = catalog_maps
        if candidates:
            require(len(catalog_maps) == 1, f'bitmap lacks exact catalog address {model_id}')
            asset = candidates[0]
            bitmap = store.read(asset.ref)
            require(Image.open(io.BytesIO(bitmap)).size == (1024, 1024), f'unexpected bitmap dimensions {model_id}')
            scale = 1000 / max(width, height)
            projection = {'scale_x': scale, 'scale_y': scale,
                          'offset_x': scene['small_map_offset_x'],
                          'offset_y': 1024 - height * scale - scene['small_map_offset_y']}
            kind = 'bitmap'
            evidence['source_asset'] = {k: v for k, v in asset.to_dict().items() if k != 'ref'}
            evidence['source_payload_sha256'] = asset.ref.payload_sha256
            evidence['source_pack_sha256'] = asset.ref.pack_sha256
            evidence['source_pack_range'] = [asset.ref.offset, asset.ref.length]
            evidence['projection_rule'] = 'Current Lua UIMain 1024px projection: X=gameX*1000/max(width,height)+offsetX; Y=1024-(height-gameY)*1000/max(width,height)-offsetY.'
            # Independent route through the audited world-space helper: prove
            # axis direction and offset math without using the output formula.
            for gx, gy in ((0, 0), (width, 0), (0, height), (width / 3, height / 7)):
                px, py = world_to_minimap(gx / 100, (height - gy) / 100, scene)
                require(abs(px - (gx * projection['scale_x'] + projection['offset_x'])) < 1e-8,
                        f'bitmap X projection disagrees {scene_id}')
                require(abs(py - (gy * projection['scale_y'] + projection['offset_y'])) < 1e-8,
                        f'bitmap Y projection disagrees {scene_id}')
        else:
            require(not catalog_maps, f'minimap exists in catalog but was not packed: {model_id}')
            row = nav_models.get(model_id)
            require(row is not None, f'no verified navigation fallback {model_id}')
            entries = [a for a in row.get('astar', []) if a.get('status') == 'parsed']
            require(len(entries) == 1, f'no unique verified Recast cache {model_id}')
            entry = entries[0]
            mesh_path = args.navigation_source / Path(entry['export'].replace('\\', '/')).name
            cache_path = args.navigation_source / Path(entry['cache_export'].replace('\\', '/')).name
            mesh_bytes, cache_bytes = mesh_path.read_bytes(), cache_path.read_bytes()
            mesh = json.loads(mesh_bytes)
            require(sha(cache_bytes) == entry['cache_sha256'] == mesh['sha256'], f'cache payload mismatch {model_id}')
            require(mesh['source']['catalog_sha256'] == catalog_meta['sha256'], f'mesh catalog mismatch {model_id}')
            require(int(mesh['source']['model']) == model_id, f'mesh model mismatch {model_id}')
            parsed = parse_navmesh(cache_bytes)
            require(all(mesh[key] == value for key, value in parsed.items()), f'parsed geometry mismatch {model_id}')
            require(mesh['source']['scene'] == row['identity'] and mesh['source']['cache'] == entry['cache_bundle']
                    and mesh['source']['reference'] == entry['cache_reference'], f'navmesh provenance mismatch {model_id}')
            verified_bundles = []
            for source in (row['identity'], entry['cache_bundle']):
                raw = Path(source['path']).read_bytes()
                binding = catalog_bundles[source['name']]
                require(len(raw) == source['bytes'] == binding['FileSize'] and sha(raw) == source['sha256']
                        and hashlib.md5(raw).hexdigest() == binding['FileHash'], f'navmesh bundle identity mismatch {model_id}')
                verified_bundles.append({'name': source['name'], 'sha256': source['sha256'], 'bytes': len(raw), 'catalog_md5': binding['FileHash']})
            bitmap, projection, nav_info = render_navigation(mesh, scene)
            kind = 'navigation'
            evidence.update({'navigation': nav_info, 'cache_payload_sha256': sha(cache_bytes),
                             'parsed_export_sha256': sha(mesh_bytes), 'cache_reference': entry['cache_reference'],
                             'source_bundles': verified_bundles,
                             'projection_rule': 'Exact Recast XZ bounds, uniform scale and centered padding; worldX=gameX/100, worldZ=(mapHeight-gameY)/100; north/up is decreasing gameY.'})
            # Validate exact vertex projection and Y orientation independently.
            bounds = nav_info['world_bounds_xz']
            rate = 920 / max(bounds[2] - bounds[0], bounds[3] - bounds[1])
            for graph in mesh['graphs']:
                for vx, _, vz in graph['world_vertices_int3']:
                    wx, wz = vx / 1000, vz / 1000
                    gx, gy = wx * 100, height - wz * 100
                    actual = (gx * projection['scale_x'] + projection['offset_x'], gy * projection['scale_y'] + projection['offset_y'])
                    expected = ((wx - (bounds[0] + bounds[2]) / 2) * rate + 512,
                                ((bounds[1] + bounds[3]) / 2 - wz) * rate + 512)
                    require(max(abs(a - b) for a, b in zip(actual, expected)) < 1e-7, f'nav projection mismatch {scene_id}')
        image = f'assets/maps/{model_id}.png'
        if image in unique_files:
            require(unique_files[image] == sha(bitmap), f'shared model bitmap mismatch {model_id}')
        else:
            (args.output / image).write_bytes(bitmap)
            unique_files[image] = sha(bitmap)
        minimap = {'type': kind, 'image': image, 'width': 1024, 'height': 1024,
                   'projection': projection, 'sha256': sha(bitmap)}
        if kind == 'navigation':
            minimap['note'] = '原圖未提供，以同版靜態導航區域繪製。'
        points = []
        if roster:
            entry = next(m for m in roster['maps'] if m['id'] == scene_id)
            require(entry['modelId'] == model_id, f'roster model mismatch {scene_id}')
            for chest in entry['chests']:
                px = chest['x'] * projection['scale_x'] + projection['offset_x']
                py = chest['y'] * projection['scale_y'] + projection['offset_y']
                points.append({'id': chest['id'], 'x': chest['x'], 'y': chest['y'], 'image_x': px, 'image_y': py,
                               'inside_image': 0 <= px <= 1024 and 0 <= py <= 1024})
        maps.append({'id': scene_id, 'name': scene['name_zh_hant'], 'model_id': model_id, 'minimap': minimap})
        evidence.update({'type': kind, 'output': image, 'output_sha256': sha(bitmap), 'output_bytes': len(bitmap),
                         'projection': projection, 'chest_projection': points})
        audit_maps.append(evidence)
    output = {'schema_version': 1, 'release_id': args.release_id, 'maps': maps}
    write_json(args.output / 'data/maps.json', output)
    write_json(args.output / 'evidence/maps.json', {
        'schema_version': 1, 'release_id': args.release_id,
        'sources': {'release_manifest_sha256': sha(release_bytes), 'packed_manifest_sha256': sha(manifest_bytes),
                    'main_catalog_sha256': catalog_meta['sha256'], 'scenes_payload_sha256': scenes_asset.ref.payload_sha256,
                    'text_payload_sha256': text_asset.ref.payload_sha256, 'navigation_inventory_sha256': sha(inventory_bytes),
                    'roster_sha256': sha(roster_path.read_bytes()) if roster else None,
                    'exporter_sha256': sha(Path(__file__).read_bytes()), 'navigation_renderer_pillow_version': pillow_version,
                    'projection_evidence_sha256': sha(projection_evidence_path.read_bytes())},
        'counts': {'maps': len(maps), 'bitmap_maps': sum(m['minimap']['type'] == 'bitmap' for m in maps),
                   'navigation_maps': sum(m['minimap']['type'] == 'navigation' for m in maps),
                   'unique_image_files': len(unique_files),
                   'image_bytes': sum((args.output / path).stat().st_size for path in unique_files)},
        'scope': 'Only requested chest-map images are exported. No runtime endpoints, raw bundles, credentials or game models are included.',
        'maps': audit_maps,
    })
    print(json.dumps({'maps': len(maps), 'unique_images': len(unique_files),
                      'bitmap_maps': sum(m['minimap']['type'] == 'bitmap' for m in maps),
                      'navigation_maps': sum(m['minimap']['type'] == 'navigation' for m in maps),
                      'out_of_bounds_chests': [p for m in audit_maps for p in m['chest_projection'] if not p['inside_image']]}))


if __name__ == '__main__':
    main()
