"""Join chest scenes to exact in-game world-map prefab anchors.

Reads an immutable WLRE packed release and export_world_assets.py evidence.
Keeps scenes without an original game pin accessible without inventing geography.
The runtime join is ScenesData.mapGroup -> WorldMapData.ID -> PortalSceneID.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path
import struct
import sys


def sha(body):
    return hashlib.sha256(body).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8', newline='\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo-root', type=Path, required=True)
    p.add_argument('--pipeline-root', type=Path, required=True)
    p.add_argument('--release-id', required=True)
    p.add_argument('--output-root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--lua-evidence', type=Path, action='append', required=True)
    args = p.parse_args()
    sys.path.insert(0, str(args.repo_root / 'src'))
    from wlre.asset_input import read_packed_textasset_at
    from wlre.analyzers.text_data import extract_strings
    from wlre.navigation.scenes import parse_scenes
    from wlre.navigation.teleport_graph import _parse_world_maps, WORLD_MAP_RECORD
    from wlre.navigation.door_links import parse_eve_links, parse_binary_door_links

    output = args.output_root.resolve()
    pipeline = args.pipeline_root.resolve()
    if output.is_relative_to(pipeline):
        raise ValueError('Output must not modify the managed pipeline')
    release = args.release_id
    if Path(release).name != release or '/' in release or '\\' in release:
        raise ValueError('Release must be a single directory name')
    manifest = pipeline / 'releases' / release / 'unity/web-runtime.packed-extraction.json'
    registry_path = args.repo_root / 'evidence/current/current-web-asset-registry.json'
    registry = json.loads(registry_path.read_text('utf8'))
    assets_path = output / 'evidence/world-assets.json'
    assets = json.loads(assets_path.read_text('utf8'))
    chest_path = output / 'data/chests.json'
    chests = json.loads(chest_path.read_text('utf8'))
    assert release == assets['release_id'] == chests['releaseId'] == registry['release']['release_id']
    assert json.loads(manifest.read_text('utf8'))['release_id'] == release

    sources = {}
    def read(name):
        body = read_packed_textasset_at(pipeline / 'asset-store', manifest, name)
        sources[name] = {'bytes': len(body), 'sha256': sha(body)}
        return body
    text_body = read('TextData_C')
    texts = {r['id']: r['string'] for r in extract_strings(text_body)}
    scene_body = read('ScenesData_C')
    scenes = {r['scene_id']: r for r in parse_scenes(scene_body, texts, include_raw_record=True)}
    world_body = read('WorldMapData_C')
    world_rows = _parse_world_maps(world_body, Path('WorldMapData_C.bytes'))
    assert WORLD_MAP_RECORD.size == 41
    assert len(world_body) == 4 + len(world_rows) * 41
    assert len({r['world_map_id'] for r in world_rows}) == len(world_rows)
    by_group = {r['world_map_id']: r for r in world_rows}
    anchors = {r['portal_scene_id']: r for r in assets['anchors']}
    canvas = assets['canvas']
    width, height = canvas['width'], canvas['height']
    assert canvas['preserve_aspect_ratio'] == 'none'
    assert canvas['pivot'] == {'x': .5, 'y': .5}

    # Retain every world-table row and its exact record, including hidden rows.
    world_evidence = []
    text_ids = set()
    for index, row in enumerate(world_rows):
        offset = 4 + index * 41
        raw = world_body[offset:offset + 41]
        assert struct.unpack_from('<H', raw, 0)[0] == row['world_map_id']
        assert struct.unpack_from('<I', raw, 2)[0] == row['map_name_text_id']
        assert raw[10] == row['open_setting']
        assert struct.unpack_from('<H', raw, 25)[0] == row['portal_scene_id']
        text_ids.add(row['map_name_text_id'])
        world_evidence.append({**row, 'name': texts[row['map_name_text_id']],
            'record_offset': offset, 'record_size': 41, 'record_hex': raw.hex()})

    text_evidence = []
    offset = 4
    for _ in range(struct.unpack_from('<I', text_body)[0]):
        tid, size = struct.unpack_from('<IH', text_body, offset)
        if tid in text_ids:
            text_evidence.append({'id': tid, 'value': texts[tid], 'record_offset': offset,
                'record_hex': text_body[offset:offset + 6 + size].hex()})
        offset += 6 + size
    assert offset == len(text_body)

    grouped = defaultdict(list)
    memberships, unmapped = [], []
    for chest_map in chests['maps']:
        sid = chest_map['id']
        scene = scenes[sid]
        group = scene['map_group']
        assert group == chest_map['mapGroup']
        raw = bytes.fromhex(scene['record_hex'])
        assert raw[0x19] == group
        assert struct.unpack_from('<H', raw)[0] == sid
        world = by_group.get(group)
        reason = None
        if not world:
            reason = 'no_world_map_group'
        elif world['open_setting'] == 1:
            reason = 'hidden_world_map_without_anchor'
        elif world['portal_scene_id'] not in anchors:
            reason = 'missing_original_world_map_anchor'
        if reason:
            entry = {'mapId': sid, 'mapGroup': group, 'reason': reason}
            if world:
                entry.update(worldMapId=world['world_map_id'], worldName=texts[world['map_name_text_id']])
            unmapped.append(entry)
        else:
            grouped[group].append(chest_map)
        memberships.append({'scene_id': sid, 'name': scene['name_zh_hant'],
            'map_group': group, 'chest_count': len(chest_map['chests']),
            'world_map_id': world['world_map_id'] if world else None,
            'portal_scene_id': world['portal_scene_id'] if world else None,
            'mapped': reason is None, 'unmapped_reason': reason,
            'record_offset': scene['record_offset'], 'record_size': scene['record_size'],
            'record_hex': raw.hex(), 'map_group_byte_offset': scene['record_offset'] + 0x19,
            'map_group_byte_hex': raw[0x19:0x1a].hex()})

    points = []
    for group, maps in sorted(grouped.items()):
        row = by_group[group]
        anchor = anchors[row['portal_scene_id']]
        assert anchor['anchor_min'] == anchor['anchor_max'] == {'x': .5, 'y': .5}
        x = width / 2 + anchor['anchored_position']['x'] + anchor['icon_offset']['x']
        y = height / 2 - anchor['anchored_position']['y'] - anchor['icon_offset']['y']
        assert abs(x - anchor['x']) < 1e-6 and abs(y - anchor['y']) < 1e-6
        points.append({'id': group, 'name': texts[row['map_name_text_id']],
            'x': x, 'y': y, 'mapIds': [m['id'] for m in maps],
            'chestCount': sum(len(m['chests']) for m in maps),
            'portalSceneId': row['portal_scene_id']})
    view = {'schemaVersion': 1, 'releaseId': release, 'image': canvas['image'],
        'width': width, 'height': height,
        'imageWidth': canvas['texture_width'], 'imageHeight': canvas['texture_height'],
        'preserveAspectRatio': 'none', 'points': points,
        'unmappedMapIds': [m['mapId'] for m in unmapped], 'unmapped': unmapped}
    covered = [sid for point in points for sid in point['mapIds']] + view['unmappedMapIds']
    assert len(set(covered)) == len(covered) == len(chests['maps']) == 57
    assert set(covered) == {m['id'] for m in chests['maps']}
    assert len(points) == 27 and len(unmapped) == 15
    assert sum(p['chestCount'] for p in points) == 43

    # Search exact current links for ungrouped scenes. Paths are diagnostic;
    # a reachable portal does not establish geographic/world-map membership.
    eve_link_body, binary_link_body = read('EveDoorLinks'), read('DoorLinkData_C')
    eve_links = parse_eve_links(eve_link_body, Path('EveDoorLinks.bytes'))
    link_rows = [{**r, 'source_asset': 'EveDoorLinks'} for rows in eve_links.values() for r in rows]
    binary_links = parse_binary_door_links(binary_link_body, Path('DoorLinkData_C.bytes'))
    link_rows += [{**r, 'source_asset': 'DoorLinkData_C'} for r in binary_links]
    adjacency = defaultdict(list)
    for row in link_rows:
        adjacency[row['source_scene_id']].append((row['destination_scene_id'], row, False))
        adjacency[row['destination_scene_id']].append((row['source_scene_id'], row, True))
    diagnostics = []
    for missing in unmapped:
        if missing['mapGroup']:
            continue
        sid = missing['mapId']
        queue = deque([(sid, [sid], [])])
        seen, frontiers = set(), {}
        while queue:
            current, path, edges = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            scene = scenes.get(current, {})
            boundary = None
            if scene.get('land_type') == 'big_map':
                boundary = 'world_scene_' + str(current)
            elif scene.get('map_group', 0):
                boundary = 'map_group_' + str(scene['map_group'])
            if boundary:
                frontiers.setdefault(boundary, {'scene_path': path,
                    'scene_names': [scenes.get(i, {}).get('name_zh_hant') for i in path], 'links': edges})
                continue
            for target, edge, reversed_edge in adjacency[current]:
                queue.append((target, path + [target], edges + [{**edge, 'traversed_reverse_for_diagnostic': reversed_edge}]))
        diagnostics.append({'scene_id': sid, 'reachable_ungrouped_and_boundary_scenes': len(seen),
            'first_boundaries': frontiers, 'assigned_from_links': False})

    # Keep bounded, hash-bound Lua evidence, not a full decrypted source corpus.
    # A previous bounded world evidence file is also a valid pinned input;
    # fresh recovery can instead supply one or more trace_lua.ps1 outputs.
    lua_inputs = [json.loads(path.read_text('utf-8-sig')) for path in args.lua_evidence]
    lua_docs = [doc.get('lua', doc) for doc in lua_inputs]
    wanted = {'Data/WorldMapData.lua', 'UI/Logic/UIWorldMap.lua', 'UI/Module/UIWorldMapModule.lua'}
    lua_sources, snippets = {}, {}
    for doc in lua_docs:
        assert doc['source_count'] == doc['roundtrips_verified'] == 866
        for source in doc['sources']:
            if source['path'] in wanted:
                if source['path'] in lua_sources:
                    assert lua_sources[source['path']] == source
                lua_sources[source['path']] = source
        for snippet in doc.get('snippets', []):
            path, line = snippet['path'], snippet['line']
            if (path == 'Data/WorldMapData.lua' and (10 <= line <= 47 or 61 <= line <= 84)) or (
                    path == 'UI/Logic/UIWorldMap.lua' and
                    (94 <= line <= 111 or 160 <= line <= 166 or 617 <= line <= 675 or 785 <= line <= 796)):
                snippets[(path, line)] = snippet
    assert ('UI/Logic/UIWorldMap.lua', 790) in snippets
    assert ('Data/WorldMapData.lua', 79) in snippets
    evidence = {'schema_version': 1, 'release_id': release,
        'bindings': {'registry_sha256': sha(registry_path.read_bytes()), 'packed_manifest_sha256': sha(manifest.read_bytes()),
            'chest_roster_sha256': sha(chest_path.read_bytes()), 'world_assets_sha256': sha(assets_path.read_bytes())},
        'sources': sources,
        'world_map_table': {'record_count': len(world_rows), 'record_size': 41, 'header_size': 4,
            'raw_hex': world_body.hex(), 'records': world_evidence},
        'localized_names': text_evidence, 'scene_memberships': memberships,
        'projection': {'width': width, 'height': height, 'formula': 'x=width/2+anchoredPosition.x+iconOffset.x; y=height/2-anchoredPosition.y-iconOffset.y',
            'preserveAspectRatio': 'none', 'asset_evidence': 'evidence/world-assets.json'},
        'coverage': {'chest_maps': 57, 'chests': 58, 'world_points': 27, 'mapped_maps': 42, 'mapped_chests': 43,
            'unmapped_maps': 15, 'unmapped_chests': 15, 'no_map_group': 14, 'hidden_world_map_without_anchor': 1},
        'mapping_authority': 'UIWorldMap._TransformStageID: scene.mapGroup -> WorldMapData.GetDataByID -> PortalSceneID -> GameObject_Map%05d',
        'lua': {'profile_id': lua_docs[0]['profile_id'], 'profile_sha256': lua_docs[0]['profile_sha256'],
            'client_identity': lua_docs[0]['client_identity'], 'source_count': 866, 'roundtrips_verified': 866,
            'sources': list(lua_sources.values()), 'snippets': [snippets[k] for k in sorted(snippets)],
            'version_boundary': 'Installed client Lua is 1.3.18; data and UI prefab are immutable managed 1.3.19. Actual source hashes are retained.'},
        'unmapped_investigation': {'eve_link_scene_count': len(eve_links),
            'eve_link_count': sum(len(rows) for rows in eve_links.values()), 'binary_link_count': len(binary_links),
            'policy': 'Undirected connectivity is diagnostic only; routes, reused templates, and names are not world-map region membership.',
            'scenes': diagnostics}}
    write_json(output / 'data/world.json', view)
    evidence['public_world_sha256'] = sha((output / 'data/world.json').read_bytes())
    write_json(output / 'evidence/world.json', evidence)
    print(json.dumps({'points': len(points), 'mappedMaps': 42, 'mappedChests': 43,
        'unmappedMaps': len(unmapped), 'totalMaps': len(covered), 'totalChests': 58,
        'worldSha256': evidence['public_world_sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
