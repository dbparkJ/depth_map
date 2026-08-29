"use strict";

const assert = require("node:assert/strict");
const viewer = require("../../services/road_condition_web/viewer_core.js");

assert.deepEqual(viewer.parseRoutePaths("chunk-b\nchunk-a,chunk-b"), ["chunk-b", "chunk-a"]);
assert.throws(() => viewer.parseRoutePaths("../private"), /상대 경로/);

const sequence = viewer.buildTileSequence([
  { dataset_id: "b", workspace_relative_path: "chunk-b", tiles: [{ tile_id: "tile-000000", state: "completed", core_start_m: 0, core_end_m: 10 }] },
  { dataset_id: "a", workspace_relative_path: "chunk-a", tiles: [{ tile_id: "tile-000000", state: "failed", core_start_m: 0, core_end_m: 10 }] }
]);
assert.deepEqual(sequence.map((item) => item.path), ["chunk-b", "chunk-a"]);
assert.equal(sequence[1].state, "failed");

assert.equal(viewer.defectVisible({ defect_type: "pothole", confidence: 0.8, quality_flags: [] }, { types: { pothole: true }, minimumConfidence: 0.5 }), true);
assert.equal(viewer.defectVisible({ defect_type: "pothole", confidence: 0.4, quality_flags: [] }, { types: { pothole: true }, minimumConfidence: 0.5 }), false);
assert.equal(viewer.defectVisible({ defect_type: "ponding_screening_proxy", confidence: 0.8, quality_flags: [] }, { showAdvanced: false }), false);
assert.equal(viewer.mapAdapterStatus("local_enu").ready, true);
assert.equal(viewer.mapAdapterStatus("vworld").ready, false);
assert.equal(viewer.mapAdapterStatus("vworld", { vworldApiKey: "key", vworldDomain: "127.0.0.1" }, { longitude_deg: 127, latitude_deg: 37 }).ready, true);
assert.equal(viewer.mapAdapterStatus("cesium").ready, false);
const origin = { longitude_deg: 127, latitude_deg: 37, ellipsoid_height_m: 50 };
const recoveredOrigin = viewer.enuToWgs84([0, 0, 0], origin);
assert.ok(Math.abs(recoveredOrigin[0] - origin.longitude_deg) < 1e-9);
assert.ok(Math.abs(recoveredOrigin[1] - origin.latitude_deg) < 1e-9);
const east = viewer.enuToWgs84([100, 0, 0], origin);
const north = viewer.enuToWgs84([0, 100, 0], origin);
assert.ok(east[0] > origin.longitude_deg);
assert.ok(north[1] > origin.latitude_deg);
const converted = viewer.enuFeatureCollectionToWgs84({
  origin,
  features: [{ id: "d1", geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [0, 1], [0, 0]]] } }]
});
assert.equal(converted.coordinate_system, "EPSG:4326");
assert.equal(converted.features[0].id, "d1");
assert.equal(converted.features[0].geometry.coordinates[0][0].length, 3);
assert.ok(viewer.perspectivePoint(0.5, 0.5, -20, 800, 460, 5).every(Number.isFinite));

const payload = new ArrayBuffer(64 + 2 * 12);
const bytes = new Uint8Array(payload);
bytes.set([82, 67, 69, 86]);
const data = new DataView(payload);
data.setUint32(4, 1, true);
data.setUint32(8, 2, true);
data.setUint32(12, 12, true);
data.setFloat32(16, 10, true);
data.setFloat32(20, 20, true);
data.setFloat32(24, 30, true);
data.setFloat32(28, 0.1, true);
data.setFloat32(32, 0.2, true);
data.setFloat32(36, 0.3, true);
data.setUint16(64, 2, true);
data.setUint16(66, 3, true);
data.setUint16(68, 4, true);
bytes.set([100, 110, 120, 1], 70);
data.setUint16(74, 7, true);
data.setUint16(76, 5, true);
data.setUint16(78, 6, true);
data.setUint16(80, 7, true);
bytes.set([130, 140, 150, 0], 82);
data.setUint16(86, 65535, true);
const evidence = viewer.parseRcev(payload);
assert.equal(evidence.count, 2);
assert.ok(Math.abs(evidence.positions[0] - 10.2) < 1e-5);
assert.ok(Math.abs(evidence.positions[4] - 21.2) < 1e-5);
assert.deepEqual([...evidence.colors.slice(0, 3)], [100, 110, 120]);
assert.equal(evidence.defectClasses[0], 1);
assert.equal(evidence.defectIndices[1], 65535);
assert.equal(viewer.evidenceDefectId({ defects: [{ index: 7, defect_id: "pothole-1" }] }, 7), "pothole-1");
assert.equal(viewer.evidenceDefectId({ defects: [] }, 65535), null);

console.log("viewer_core contract: ok");
