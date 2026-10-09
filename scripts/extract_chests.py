"""Extract all gold-chest local slots from one immutable WLRE release.

Requires Python 3.12+, the WLRE repository and its declared dependencies.
Reads packed assets only; does not launch a game or modify the managed pipeline.
Event grammar follows the repository's audited EventData loader. Crucially,
eventNumber is the runtime scene key; mapNumber is retained separately.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys


def sha(body):
    return hashlib.sha256(body).hexdigest()


class Reader:
    def __init__(self, body, start, end):
        self.body, self.pos, self.end = body, start, end

    def take(self, size):
        if size < 0 or self.pos + size > self.end:
            raise ValueError('Eve section boundary exceeded')
        value = self.body[self.pos:self.pos + size]
        self.pos += size
        return value

    def num(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]

    def counted(self, parser):
        return [parser() for _ in range(self.num('B'))]

    def name(self):
        raw = self.take(20)
        try:
            name = raw[1:1 + raw[0]].decode('big5') if raw[0] <= 19 else None
        except UnicodeDecodeError:
            name = None
        return {'editor_name': name, 'editor_name_hex': raw.hex()}

    def common(self):
        row = {'slot': self.num('H'), **self.name(), 'baseDefine': self.num('B'),
               'raw_x': self.num('I'), 'raw_y': self.num('I')}
        row['event_ids'] = list(self.take(self.num('B')))
        row['lead_event_ids'] = list(self.take(self.num('B')))
        row['sign_icon'] = self.num('B')
        return row

    def npc(self):
        start = self.pos
        row = self.common()
        row.update(npc_id=self.num('I'), direction=self.num('B'),
                   move_mode=self.num('B'), go_back_mode=self.num('B'))
        row['nodes_raw'] = self.counted(lambda: list(struct.unpack('<iiI', self.take(12))))
        row.update(move_speed=self.num('B'), trace_mode=self.num('B'))
        row['opaque_before_paths_hex'] = self.take(11).hex()
        row['parametric_paths_hex'] = self.counted(lambda: self.take(103).hex())
        row['opaque_tail_hex'] = self.take(38).hex()
        row['record_offset'] = start
        row['record_bytes'] = self.pos - start
        row['record_sha256'] = sha(self.body[start:self.pos])
        # A separate direct binary read independently checks the Reader's fields.
        assert struct.unpack_from('<H', self.body, start)[0] == row['slot']
        assert struct.unpack_from('<II', self.body, start + 23) == (row['raw_x'], row['raw_y'])
        row['coordinate_byte_evidence'] = {
            'absolute_offset': start + 23,
            'hex_u32le_x_y': self.body[start + 23:start + 31].hex(),
            'independent_unpack_passed': True,
        }
        return row

    def battle(self):
        row = self.common()
        row.update(music_type=self.num('B'), link_number=self.num('H'), cant_join_raw=self.num('B'))
        def member():
            return {'npc_id': self.num('H'), 'index': self.num('B')}
        row['left_members'] = self.counted(member)
        row['right_members'] = self.counted(member)
        row['cant_win_raw'] = self.num('B')
        return row

    def clause(self, result=False):
        raw = self.take(22)
        keys = ('id', 'paraMain', 'paraKind', 'paraStyle', 'paraType',
                'paraOperator', 'paraValue', 'groupNumber', 'paraClass1', 'paraClass2')
        fmt = '<BBHHHBiBII' if result else '<BBHHHBIBII'
        return {'raw_hex': raw.hex(), **dict(zip(keys, struct.unpack(fmt, raw)))}

    def event(self):
        start = self.pos
        row = {'index': self.num('H'), 'occur_type': self.num('B'), **self.name()}
        def condition():
            return {'condition': self.clause(), 'results': self.counted(lambda: self.clause(True))}
        row['conditions'] = self.counted(condition)
        row.update(record_offset=start, record_sha256=sha(self.body[start:self.pos]))
        return row


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo-root', type=Path, required=True)
    p.add_argument('--pipeline-root', type=Path, required=True)
    p.add_argument('--release-id', required=True)
    p.add_argument('--output-root', type=Path, required=True)
    p.add_argument('--lua-evidence', type=Path)
    args = p.parse_args()
    sys.path.insert(0, str(args.repo_root / 'src'))
    from wlre.asset_input import read_packed_textasset_at
    from wlre.analyzers.audit_full_package_loaders import framing_metadata
    from wlre.analyzers.npcs import parse_npcs
    from wlre.analyzers.text_data import extract_strings
    from wlre.analyzers.talk import parse_talk
    from wlre.analyzers.items import build_catalog
    from wlre.navigation.scenes import parse_scenes
    from wlre.navigation.stage_maps import parse_stage_maps

    pipeline = args.pipeline_root.resolve()
    output = args.output_root.resolve()
    if output.is_relative_to(pipeline):
        raise ValueError('Output must be outside the immutable pipeline')
    release = args.release_id
    if Path(release).name != release or '/' in release or '\\' in release:
        raise ValueError('Release must be a single directory name')
    manifest = pipeline / 'releases' / release / 'unity/web-runtime.packed-extraction.json'
    identity = json.loads(manifest.read_text('utf8'))
    registry_path = args.repo_root / 'evidence/current/current-web-asset-registry.json'
    registry = json.loads(registry_path.read_text('utf8'))
    if identity['release_id'] != release or registry['release']['release_id'] != release:
        raise ValueError('Registry and packed release disagree')
    build_paths = list((pipeline / 'build/results').glob('*.json'))
    receipts = [x for x in build_paths if (lambda r: r.get('release_id') == release and r.get('status') == 'complete')(json.loads(x.read_text('utf8')))]
    if not receipts:
        raise ValueError('No completed build receipt for release')
    build_receipt = max(receipts, key=lambda x: x.stat().st_mtime_ns)
    sources = {}
    def read(name):
        value = read_packed_textasset_at(pipeline / 'asset-store', manifest, name)
        sources[name] = {'bytes': len(value), 'sha256': sha(value)}
        return value

    definitions = parse_npcs(read('NpcData_C'))
    npcs = {r['id']: r for r in definitions}
    texts = {r['id']: r['string'] for r in extract_strings(read('TextData_C'))}
    talks = {r['id']: r['text'] for r in parse_talk(read('TalkData_C'))}
    scene_bytes = read('ScenesData_C')
    scenes = {r['scene_id']: r for r in parse_scenes(scene_bytes, texts, include_raw_record=True)}
    stage_maps = parse_stage_maps(read('StageMapData_C'), texts=texts)
    items = {r['id']: r for c in build_catalog(read('ItemData_C'), include_set_metadata=False)['categories'] for r in c['items']}
    exact = [r['id'] for r in definitions if r['name_zh_hant'] == '金色寶箱']
    target_ids = [r['id'] for r in definitions if r['name_zh_hant'] == '黃金寶箱']
    assert target_ids == [19117], target_ids
    assert npcs[16011]['name_zh_hant'] == '黃金大寶箱'
    body = read('Eve')
    audit = framing_metadata('Eve', body, None)
    for key in ('exact_eof', 'header_spans_contiguous', 'all_scene_bodies_strictly_validated'):
        assert audit[key], key
    maps, evidence_maps, separate, battle_hits, broad_counts = [], [], [], [], Counter()
    all_ids = set()
    for header_index in range(audit['file_count_u32']):
        eid, map_number, start, size = struct.unpack_from('<HHII', body, 12 + header_index * 12)
        assert eid not in all_ids
        all_ids.add(eid)
        offsets = struct.unpack_from('<11I', body, start + size - 44)
        def section(index, method):
            reader = Reader(body, start + offsets[index], start + offsets[index + 1])
            rows = [getattr(reader, method)() for _ in range(reader.num('H'))]
            assert reader.pos == reader.end, (eid, method)
            return rows
        placements, battles = section(0, 'npc'), section(8, 'battle')
        for row in placements:
            if '寶箱' in npcs.get(row['npc_id'], {}).get('name_zh_hant', ''):
                broad_counts[row['npc_id']] += 1
            if row['npc_id'] == 16011:
                separate.append({'scene_id': eid, 'map_number': map_number,
                                 'scene_name': scenes[eid]['name_zh_hant'], **row})
        for row in battles:
            if any(m['npc_id'] in target_ids for m in row['left_members'] + row['right_members']):
                battle_hits.append({'scene_id': eid, **row})
        selected = [r for r in placements if r['npc_id'] in target_ids]
        if not selected:
            continue
        scene = scenes[eid]  # Runtime key is eventNumber, NEVER mapNumber.
        events, leads = section(4, 'event'), section(9, 'event')
        event_lookup = {r['index']: r for r in events}
        lead_lookup = {r['index']: r for r in leads}
        ratio_x, ratio_y = scene['position_x_ratio'], scene['position_y_ratio']
        so = scene['record_offset']
        assert scene_bytes[so + 0x39:so + 0x3B] == bytes([ratio_x, ratio_y])
        scene['ratio_byte_evidence'] = {'absolute_offset': so + 0x39, 'hex': bytes([ratio_x, ratio_y]).hex()}
        view_chests = []
        for row in selected:
            row['game_x'], row['game_y'] = row['raw_x'] * ratio_x, row['raw_y'] * ratio_y
            linked_events = [event_lookup[i] for i in row['event_ids']]
            row['direct_events'] = linked_events
            row['direct_lead_events'] = [lead_lookup[i] for i in row['lead_event_ids']]
            key_ids = sorted({c['condition']['paraType'] for e in linked_events for c in e['conditions']
                              if c['condition']['paraMain'] == 2 and c['condition']['paraKind'] == 1
                              and c['condition']['paraStyle'] == 1 and c['condition']['paraType'] in (30099, 30139)})
            conditions = []
            if key_ids:
                conditions.append('事件含：' + '／'.join(items[i]['name'].rstrip('^') for i in key_ids))
            if not row['event_ids']:
                conditions.append('此本地位置未附互動事件')
            view_chests.append({'id': f'{eid}-{row["slot"]}', 'npcId': row['npc_id'],
                'name': npcs[row['npc_id']]['name_zh_hant'], 'slot': row['slot'],
                'x': row['game_x'], 'y': row['game_y'], 'rawX': row['raw_x'], 'rawY': row['raw_y'],
                'eventIds': row['event_ids'], 'leadEventIds': row['lead_event_ids'],
                'hasInteraction': bool(row['event_ids']), 'keyIds': key_ids, 'conditions': conditions})
        maps.append({'id': eid, 'name': scene['name_zh_hant'], 'region': f'分區 {scene["map_group"]}' if scene['map_group'] else '未分區',
                     'mapGroup': scene['map_group'], 'modelId': scene['model_id'], 'eveMapNumber': map_number,
                     'mapExtent': {'width': scene['map_extent']['map_width'], 'height': scene['map_extent']['map_height']},
                     'ratio': {'x': ratio_x, 'y': ratio_y},
                     'smallMapOffset': {'x': scene['small_map_offset_x'], 'y': scene['small_map_offset_y']},
                     'chests': view_chests})
        evidence_maps.append({'scene_id': eid, 'eve_map_number': map_number,
            'eve_header_offset': 12 + header_index * 12, 'eve_header_hex': body[12 + header_index * 12:24 + header_index * 12].hex(),
            'scene_body_offset': start, 'scene_body_size': size, 'scene_body_sha256': sha(body[start:start + size]),
            'scene_metadata': scene, 'npcs': selected})
    maps.sort(key=lambda r: r['id'])
    evidence_maps.sort(key=lambda r: r['scene_id'])
    coverage = {'eveScenes': audit['file_count_u32'], 'sceneDefinitions': len(scenes),
                'npcDefinitions': len(definitions), 'maps': len(maps), 'chests': sum(len(m['chests']) for m in maps),
                'withInteraction': sum(c['hasInteraction'] for m in maps for c in m['chests']), 'battleReferences': len(battle_hits)}
    evidence = {'schemaVersion': 1, 'releaseId': release, 'clientVersion': registry['release']['client_version'],
        'bindings': {'registry_sha256': sha(registry_path.read_bytes()), 'packed_manifest_sha256': sha(manifest.read_bytes()),
                     'completed_build_receipt': build_receipt.name, 'completed_build_sha256': sha(build_receipt.read_bytes())},
        'sources': sources, 'coverage': coverage, 'strictAudit': audit,
        'scope': {'query': '金色寶箱', 'exact_name_npc_ids': exact, 'resolved_npc_ids': target_ids,
                  'actual_name': '黃金寶箱', 'name_link': {str(i): talks[i] for i in (32512,32513,32515)},
                  'excluded_distinct_name': '黃金大寶箱', 'excluded_npc_id': 16011},
        'npcDefinitions': [npcs[i] for i in target_ids],
        'relatedKeyItems': [{k: items[i][k] for k in ('id','name','desc')} for i in (30099,30139)],
        'otherChestNpcCounts': [{'npc_id': i, 'name': npcs[i]['name_zh_hant'], 'placements': n} for i,n in sorted(broad_counts.items())],
        'separateGoldenLargeChestPlacements': separate, 'battleReferences': battle_hits,
        'stageMapGoldMarkers': [r for r in stage_maps if r.get('npc_id') in target_ids],
        'sceneJoin': {'runtime_key': 'Eve header eventNumber -> ScenesData.scene_id',
                     'retained_secondary_key': 'Eve mapNumber',
                     'template_reuse_is_not_duplicate': [10161,10162]},
        'limitations': ['These are shipped local placements, not measured live server positions or availability.',
                       'Condition/result bytes and direct event links are preserved; group closure, reward semantics and reset timing are not inferred.',
                       'Installed Lua was verified separately from accepted server assets; it is compatible loader evidence, not proof of a 1.3.19 executable.',
                       'One local slot has no direct interaction event and remains visible as qualified local evidence.'],
        'maps': evidence_maps}
    if args.lua_evidence:
        lua = json.loads(args.lua_evidence.read_text('utf-8-sig'))
        ranges = {'Logic/Data.lua': [(864,870),(900,906),(1000,1010),(1025,1032)], 'Data/EventData.lua': [(59,69)]}
        evidence['luaVerification'] = {'profile_id': lua['profile_id'], 'profile_sha256': lua['profile_sha256'],
            'client_identity': lua['client_identity'], 'source_count': lua['source_count'], 'roundtrips_verified': lua['roundtrips_verified'],
            'sources': [r for r in lua['sources'] if r['path'] in ranges],
            'snippets': [r for r in lua['snippets'] if r['path'] in ranges and any(a <= r['line'] <= b for a,b in ranges[r['path']])]}
    view = {'schemaVersion': 1, 'releaseId': release, 'clientVersion': registry['release']['client_version'],
        'sourceNote': '遊戲名稱：黃金寶箱。依 1.3.19 本地事件資料整理；是否可開啟及即時出現狀態以遊戲伺服器為準。',
        'coverage': coverage, 'maps': maps}
    dump(output / 'data/chests.json', view)
    dump(output / 'evidence/chests.json', evidence)
    print(json.dumps({'coverage': coverage, 'files': ['data/chests.json','evidence/chests.json']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
