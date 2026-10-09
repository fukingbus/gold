"""Export catalog-verified world artwork and exact UI_WorldMap anchors.

Inputs are read only. The UI bundle must be supplied separately; no bundle is
published. Requires UnityPy. All PPtr identifiers are serialized as strings to
preserve their signed 64-bit values in JavaScript consumers.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import zlib

sys.dont_write_bytecode = True


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pipeline', type=Path, required=True)
    parser.add_argument('--ui-bundle', type=Path, required=True)
    parser.add_argument('--texture-bundle', type=Path)
    parser.add_argument('--auxiliary-output', type=Path,
                        help='Optional external evidence directory for unused BG/Compass PNGs')
    parser.add_argument('--release-id', default='1.3.19_d6bc1e25_e7df651c')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    import UnityPy
    import PIL

    release_path = args.pipeline / 'releases' / args.release_id / 'release.json'
    release_bytes = release_path.read_bytes()
    release = json.loads(release_bytes)
    assert release['identity']['release_id'] == args.release_id
    catalog_metadata = next(m for m in release['metadata'] if m['logical_id'] == 'MainPackage.json')
    catalog_bytes = (args.pipeline / 'cache' / catalog_metadata['cache_object']).read_bytes()
    assert digest(catalog_bytes) == catalog_metadata['sha256']
    catalog = json.loads(catalog_bytes)
    assets = {a['Address']: a for a in catalog['AssetList']}

    if args.texture_bundle is None:
        # This immutable cached object was independently rediscovered and matches
        # the current release catalog, despite originally entering an older cache.
        args.texture_bundle = args.pipeline / 'cache/bundles/sha256/fd9ce684af37f6fedac2722ccc0d4061da2f30583d83d5d9b71ec0bb5a18a656'

    def read_bundle(path: Path, address: str) -> tuple[object, dict]:
        asset = assets[address]
        catalog_bundle = catalog['BundleList'][asset['BundleID']]
        body = path.read_bytes()
        assert len(body) == catalog_bundle['FileSize']
        assert hashlib.md5(body).hexdigest() == catalog_bundle['FileHash']
        assert zlib.crc32(body).to_bytes(4, 'little').hex() == catalog_bundle['FileCRC']
        metadata = {'logical_id': catalog_bundle['BundleName'], 'sha256': digest(body),
                    'md5': hashlib.md5(body).hexdigest(), 'bytes': len(body),
                    'catalog_crc32_little_endian': catalog_bundle['FileCRC'],
                    'source_url': release['asset_root_url'].rstrip('/') + '/' + catalog_bundle['BundleName']}
        return UnityPy.load(body), metadata

    texture_environment, texture_source = read_bundle(args.texture_bundle, 'WorldMap2D_Map')
    ui_environment, ui_source = read_bundle(args.ui_bundle, 'UI_WorldMap')
    texture_objects = {o.path_id: o for o in texture_environment.objects}
    ui_objects = {o.path_id: o for o in ui_environment.objects}
    prefab_address = assets['UI_WorldMap']['AssetPath']
    prefab_ptr = ui_environment.container[prefab_address]
    prefab_object = ui_objects[prefab_ptr.path_id]
    prefab = prefab_object.read_typetree()
    root_transform = next(c['component']['m_PathID'] for c in prefab['m_Component']
                          if ui_objects[c['component']['m_PathID']].type.name == 'RectTransform')
    rows = []

    def walk(path_id: int, parent_path: str) -> None:
        obj = ui_objects[path_id]
        transform = obj.read_typetree()
        go = ui_objects[transform['m_GameObject']['m_PathID']].read_typetree()
        path = parent_path + '/' + go['m_Name']
        rows.append({'path': path, 'transform': transform, 'object': obj, 'go': go})
        for child in transform['m_Children']:
            assert child['m_FileID'] == 0
            walk(child['m_PathID'], path)

    walk(root_transform, '')
    map_node = next(r for r in rows if r['path'].endswith('/RawImage_Map'))
    rect = map_node['transform']
    width, height = rect['m_SizeDelta']['x'], rect['m_SizeDelta']['y']
    assert rect['m_Pivot'] == {'x': 0.5, 'y': 0.5}
    assert rect['m_LocalScale'] == {'x': 1.0, 'y': 1.0, 'z': 1.0}
    images = []
    for logical_name, node_name in [('WorldMap2D_Map', 'RawImage_Map'),
                                    ('WorldMap2D_BG', 'RawImage_BG'),
                                    ('WorldMap2D_Compass', 'RawImage_MapCompass')]:
        node = next(r for r in rows if r['path'].endswith('/' + node_name))
        components = [ui_objects[c['component']['m_PathID']] for c in node['go']['m_Component']]
        raw_image_obj = next(o for o in components if o.type.name == 'MonoBehaviour'
                             and 'm_Texture' in o.read_typetree())
        raw_image = raw_image_obj.read_typetree()
        pointer = raw_image['m_Texture']
        external_path = prefab_object.assets_file.externals[pointer['m_FileID'] - 1].path
        texture_obj = texture_objects[pointer['m_PathID']]
        assert external_path.rsplit('/', 1)[-1] == texture_obj.assets_file.name
        texture = texture_obj.read()
        assert texture.m_Name == logical_name
        assert raw_image['m_UVRect'] == {'x': 0.0, 'y': 0.0, 'width': 1.0, 'height': 1.0}
        buffer = io.BytesIO()
        texture.image.convert('RGBA').save(buffer, format='PNG', optimize=False)
        encoded = buffer.getvalue()
        published = logical_name == 'WorldMap2D_Map'
        image_path = args.output / 'assets/world' / (logical_name + '.png') if published else None
        target = image_path or (args.auxiliary_output / (logical_name + '.png') if args.auxiliary_output else None)
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(encoded)
        images.append({'logical_name': logical_name, 'image': image_path.relative_to(args.output).as_posix() if published else None,
                       'published': published,
                       'width': texture.m_Width, 'height': texture.m_Height,
                       'bytes': len(encoded), 'sha256': digest(encoded),
                       'asset_address': assets[logical_name]['AssetPath'],
                       'texture_assets_file': texture_obj.assets_file.name,
                       'texture_path_id': str(texture_obj.path_id),
                       'texture_object_sha256': digest(texture_obj.get_raw_data()),
                       'raw_image_path': node['path'], 'raw_image_component_path_id': str(raw_image_obj.path_id),
                       'raw_image_object_sha256': digest(raw_image_obj.get_raw_data()),
                       'pptr': {'file_id': pointer['m_FileID'], 'path_id': str(pointer['m_PathID']), 'external': external_path},
                       'uv_rect': raw_image['m_UVRect']})

    anchors = []
    by_path = {r['path']: r for r in rows}
    for row in rows:
        match = re.fullmatch(r'GameObject_Map(\d+)', row['go']['m_Name'])
        if not match:
            continue
        transform = row['transform']
        assert row['path'].rsplit('/', 1)[0] == map_node['path']
        assert transform['m_AnchorMin'] == transform['m_AnchorMax'] == {'x': 0.5, 'y': 0.5}
        assert transform['m_LocalScale'] == {'x': 1.0, 'y': 1.0, 'z': 1.0}
        pos = transform['m_AnchoredPosition']
        scene_id = int(match[1])
        button_row = by_path[row['path'] + '/GameObject_Btn' + match[1]]
        icon_row = by_path[button_row['path'] + '/Image_' + match[1]]
        button, icon = button_row['transform'], icon_row['transform']
        offset = {'x': 0.0, 'y': 0.0}
        for parent, child in [(transform, button), (button, icon)]:
            assert child['m_AnchorMin'] == child['m_AnchorMax']
            assert child['m_LocalScale'] == {'x': 1.0, 'y': 1.0, 'z': 1.0}
            for axis in offset:
                offset[axis] += child['m_AnchoredPosition'][axis] + (
                    child['m_AnchorMin'][axis] - parent['m_Pivot'][axis]) * parent['m_SizeDelta'][axis]
        assert icon['m_Pivot'] == {'x': 0.5, 'y': 0.5}
        def child_evidence(child_row):
            t = child_row['transform']
            return {'path': child_row['path'], 'transform_path_id': str(child_row['object'].path_id),
                    'transform_object_sha256': digest(child_row['object'].get_raw_data()),
                    **{k: t[k] for k in ['m_AnchorMin', 'm_AnchorMax', 'm_Pivot', 'm_SizeDelta', 'm_AnchoredPosition']}}
        anchors.append({'portal_scene_id': int(match[1]), 'prefab_path': row['path'],
                        'transform_path_id': str(row['object'].path_id),
                        'transform_object_sha256': digest(row['object'].get_raw_data()),
                        'anchored_position': pos, 'anchor_min': transform['m_AnchorMin'],
                        'anchor_max': transform['m_AnchorMax'], 'pivot': transform['m_Pivot'],
                        'size_delta': transform['m_SizeDelta'],
                        'base_x': width / 2 + pos['x'], 'base_y': height / 2 - pos['y'],
                        'icon_offset': offset, 'button_transform': child_evidence(button_row),
                        'icon_transform': child_evidence(icon_row),
                        'x': width / 2 + pos['x'] + offset['x'],
                        'y': height / 2 - pos['y'] - offset['y']})
    evidence = {'schema_version': 1, 'release_id': args.release_id,
                'release_manifest_sha256': digest(release_bytes), 'main_catalog_sha256': digest(catalog_bytes),
                'unitypy_version': UnityPy.__version__, 'pillow_version': PIL.__version__,
                'bundles': [texture_source, ui_source], 'images': images,
                'prefab': {'asset_address': prefab_address, 'assets_file': prefab_object.assets_file.name,
                           'game_object_path_id': str(prefab_object.path_id),
                           'game_object_sha256': digest(prefab_object.get_raw_data()),
                           'node_count': len(rows)},
                'canvas': {'width': width, 'height': height, 'path': map_node['path'],
                           'transform_path_id': str(map_node['object'].path_id),
                           'transform_object_sha256': digest(map_node['object'].get_raw_data()),
                           'pivot': rect['m_Pivot'], 'uv_rect': images[0]['uv_rect'],
                           'image': images[0]['image'], 'texture_width': images[0]['width'],
                           'texture_height': images[0]['height'], 'preserve_aspect_ratio': 'none',
                           'projection': 'base_x = 2580/2 + anchoredPosition.x; base_y = 1890/2 - anchoredPosition.y; x = base_x + icon_offset.x; y = base_y - icon_offset.y'},
                'anchor_count': len(anchors), 'anchors': sorted(anchors, key=lambda x: x['portal_scene_id']),
                'notes': ['Only exact UI_WorldMap direct-child GameObject_Map{PortalSceneID} anchors are exported.',
                          'ShowX/ShowY in WorldMapData are displayed travel coordinates, not artwork positions.',
                          'The client stretches the square texture to its authored 2580 x 1890 rect; keep that aspect.',
                          'Client map focus follows BaseObject pivot; exported markers follow exact Image_{PortalSceneID} icon centers, including tiny authored 0.30/0.38 X offsets.',
                          'No separate region artwork is referenced by this prefab. Submap navigation uses the existing exact scene minimaps.']}
    write_json(args.output / 'evidence/world-assets.json', evidence)
    print(json.dumps({'images': len(images), 'anchors': len(anchors), 'canvas': [width, height],
                      'image_bytes': sum(i['bytes'] for i in images)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
