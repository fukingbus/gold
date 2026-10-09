import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const load = async path => JSON.parse(await readFile(resolve(root, path), 'utf8'));
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const world = await load('data/world.json');
const roster = await load('data/chests.json');
const byId = new Map(roster.maps.map(map => [map.id, map]));
assert.equal(world.schemaVersion, 1);
assert.equal(world.releaseId, roster.releaseId);
assert.equal(world.width, 2580, 'Actual RawImage_Map UI width');
assert.equal(world.height, 1890, 'Actual RawImage_Map UI height');
assert.equal(world.imageWidth, 2048);
assert.equal(world.imageHeight, 2048);
assert.match(world.image, /^assets\/world\/[\w.-]+\.png$/);
const image = await readFile(resolve(root, world.image));
assert.equal(image.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
assert.equal(image.readUInt32BE(16), world.imageWidth);
assert.equal(image.readUInt32BE(20), world.imageHeight);
assert.equal(world.points.length, 27);

const used = new Set();
const pointIds = new Set();
let pinnedChests = 0;
for (const point of world.points) {
  assert.ok(!pointIds.has(point.id), `Duplicate world point ${point.id}`);
  pointIds.add(point.id);
  assert.ok(Number.isFinite(point.x) && point.x >= 0 && point.x <= world.width);
  assert.ok(Number.isFinite(point.y) && point.y >= 0 && point.y <= world.height);
  assert.ok(point.mapIds.length > 0);
  let count = 0;
  for (const id of point.mapIds) {
    const map = byId.get(id);
    assert.ok(map, `Unknown scene ${id}`);
    assert.ok(!used.has(id), `Duplicated scene ${id}`);
    assert.equal(map.mapGroup, point.id, `Direct client mapGroup join ${id}`);
    used.add(id);
    count += map.chests.length;
  }
  assert.equal(point.chestCount, count, `Chest counter ${point.name}`);
  pinnedChests += count;
}
assert.equal(used.size, 42);
assert.equal(pinnedChests, 43);
assert.equal(world.unmappedMapIds.length, 15);
let otherChests = 0;
for (const id of world.unmappedMapIds) {
  const map = byId.get(id);
  assert.ok(map);
  assert.ok(!used.has(id), `Duplicate other scene ${id}`);
  used.add(id);
  otherChests += map.chests.length;
  assert.ok(map.mapGroup === 0 || map.mapGroup === 42, `Unexpected absent world point ${id}`);
}
assert.equal(otherChests, 15);
assert.deepEqual(used, new Set(byId.keys()), 'Every scene is reachable through the world navigator');
assert.equal(pinnedChests + otherChests, 58);
assert.ok(world.unmappedMapIds.includes(10161), 'Do not infer Antarctica from a reused Australia template');
assert.ok(world.unmappedMapIds.includes(10162));
assert.ok(world.unmappedMapIds.includes(15103), 'Hidden WorldMap42 has no authored world pin');

// Independent byte and prefab checks are below; they bind displayed labels,
// membership and coordinates to exported source evidence instead of screenshots.
const assets = await load('evidence/world-assets.json');
assert.equal(assets.release_id, world.releaseId);
assert.equal(assets.canvas.width, world.width);
assert.equal(assets.canvas.height, world.height);
assert.deepEqual(assets.canvas.uv_rect, { x: 0, y: 0, width: 1, height: 1 });
const publishedImages = assets.images.filter(row => row.published !== false);
assert.equal(publishedImages.length, 1, 'Only the used world bitmap is published');
const picture = publishedImages.find(row => row.image === world.image);
assert.ok(picture);
assert.equal(sha(image), picture.sha256);
assert.equal(image.length, picture.bytes);
assert.equal(picture.pptr.path_id, picture.texture_path_id, 'Exact native RawImage texture PPtr');
assert.deepEqual(new Set(await readdir(resolve(root, 'assets/world'))), new Set([world.image.split('/').at(-1)]));
const anchors = new Map(assets.anchors.map(row => [row.portal_scene_id, row]));
assert.equal(anchors.size, 40);
for (const row of anchors.values()) {
  assert.deepEqual(row.anchor_min, { x: .5, y: .5 });
  assert.deepEqual(row.anchor_max, { x: .5, y: .5 });
  const button = row.button_transform;
  const icon = row.icon_transform;
  for (const axis of ['x', 'y']) {
    assert.equal(button.m_AnchorMin[axis], button.m_AnchorMax[axis]);
    assert.equal(icon.m_AnchorMin[axis], icon.m_AnchorMax[axis]);
    const buttonPivot = (button.m_AnchorMin[axis] - row.pivot[axis]) * row.size_delta[axis] + button.m_AnchoredPosition[axis];
    const iconPivot = (icon.m_AnchorMin[axis] - button.m_Pivot[axis]) * button.m_SizeDelta[axis] + icon.m_AnchoredPosition[axis];
    const center = (0.5 - icon.m_Pivot[axis]) * icon.m_SizeDelta[axis];
    assert.ok(Math.abs(buttonPivot + iconPivot + center - row.icon_offset[axis]) < 1e-8);
  }
  assert.equal(row.base_x, world.width / 2 + row.anchored_position.x);
  assert.equal(row.base_y, world.height / 2 - row.anchored_position.y);
  assert.equal(row.x, row.base_x + row.icon_offset.x);
  assert.equal(row.y, row.base_y - row.icon_offset.y);
}
for (const point of world.points) {
  const native = anchors.get(point.portalSceneId);
  assert.ok(native, `Exact prefab anchor ${point.id}`);
  assert.equal(point.x, native.x);
  assert.equal(point.y, native.y);
}
const evidence = await load('evidence/world.json');
assert.equal(evidence.release_id, world.releaseId);
assert.equal(evidence.public_world_sha256, sha(await readFile(resolve(root, 'data/world.json'))));
assert.equal(evidence.bindings.chest_roster_sha256, sha(await readFile(resolve(root, 'data/chests.json'))));
assert.equal(evidence.bindings.world_assets_sha256, sha(await readFile(resolve(root, 'evidence/world-assets.json'))));
const raw = Buffer.from(evidence.world_map_table.raw_hex, 'hex');
assert.equal(raw.length, 4 + 42 * 41);
assert.equal(raw.readInt32LE(0), 42);
assert.equal(sha(raw), evidence.sources.WorldMapData_C.sha256);
const names = new Map();
for (const name of evidence.localized_names) {
  const bytes = Buffer.from(name.record_hex, 'hex');
  assert.equal(bytes.readUInt32LE(0), name.id);
  assert.equal(bytes.length, 6 + bytes.readUInt16LE(4));
  assert.equal(bytes.subarray(6).toString('utf16le'), name.value);
  names.set(name.id, name.value);
}
const nativePoints = new Map();
for (let index = 0; index < 42; index++) {
  const bytes = raw.subarray(4 + index * 41, 4 + (index + 1) * 41);
  const row = evidence.world_map_table.records[index];
  assert.equal(row.record_offset, 4 + index * 41);
  assert.equal(bytes.toString('hex'), row.record_hex);
  assert.equal(bytes.readUInt16LE(0), row.world_map_id);
  assert.equal(bytes.readUInt32LE(2), row.map_name_text_id);
  assert.equal(bytes[10], row.open_setting);
  assert.equal(bytes.readUInt16LE(25), row.portal_scene_id);
  assert.equal(names.get(row.map_name_text_id), row.name);
  nativePoints.set(row.world_map_id, row);
}
assert.equal(evidence.scene_memberships.length, 57);
const sceneIds = new Set();
for (const scene of evidence.scene_memberships) {
  const bytes = Buffer.from(scene.record_hex, 'hex');
  const map = byId.get(scene.scene_id);
  assert.ok(map);
  assert.ok(!sceneIds.has(scene.scene_id));
  sceneIds.add(scene.scene_id);
  assert.equal(bytes.length, 76);
  assert.equal(bytes.readUInt16LE(0), map.id);
  assert.equal(bytes.readUInt16LE(8), map.modelId);
  assert.equal(bytes[0x19], map.mapGroup);
  assert.equal(scene.map_group, bytes[0x19]);
  assert.equal(scene.map_group_byte_offset, scene.record_offset + 0x19);
  assert.equal(scene.map_group_byte_hex, bytes.subarray(0x19, 0x1a).toString('hex'));
  assert.equal(scene.chest_count, map.chests.length);
  const point = world.points.find(point => point.mapIds.includes(map.id));
  assert.equal(scene.mapped, Boolean(point));
  if (point) {
    const native = nativePoints.get(bytes[0x19]);
    assert.equal(point.id, native.world_map_id);
    assert.equal(point.name, native.name);
    assert.equal(point.portalSceneId, native.portal_scene_id);
    assert.equal(scene.portal_scene_id, point.portalSceneId);
  } else {
    assert.ok(world.unmappedMapIds.includes(map.id));
    assert.ok(scene.unmapped_reason);
  }
}
console.log('Verified 27 world counters (43 chests), 15 other-map entries, exact 57-scene/58-chest coverage and native UI dimensions.');
console.log('Verified complete WorldMapData bytes, 57 raw scene memberships, UTF-16 names, native marker transforms and image hash.');
