#!/usr/bin/env python3
import json
import sys
from pathlib import Path


SCAIL_SUBGRAPH_ID = "ab27c382-c076-424b-b976-d1bc3f88ba12"


def fail(message):
    raise SystemExit("MeetMap workflow validation failed: " + message)


def validate_node_links(nodes, links, scope):
    by_id = {node.get("id"): node for node in nodes}
    seen_link_ids = set()

    for link in links:
        if isinstance(link, dict):
            link_id = link.get("id")
            origin_id = link.get("origin_id")
            origin_slot = link.get("origin_slot")
            target_id = link.get("target_id")
            target_slot = link.get("target_slot")
        else:
            if len(link) < 5:
                fail(f"{scope}: malformed link {link!r}")
            link_id, origin_id, origin_slot, target_id, target_slot = link[:5]

        if link_id in seen_link_ids:
            fail(f"{scope}: duplicate link id {link_id}")
        seen_link_ids.add(link_id)

        if origin_id is not None and origin_id >= 0:
            origin = by_id.get(origin_id)
            if origin is None:
                fail(f"{scope}: link {link_id} missing origin node {origin_id}")
            outputs = origin.get("outputs") or []
            if not isinstance(origin_slot, int) or origin_slot < 0 or origin_slot >= len(outputs):
                fail(f"{scope}: link {link_id} invalid origin slot {origin_id}:{origin_slot}")

        if target_id is not None and target_id >= 0:
            target = by_id.get(target_id)
            if target is None:
                fail(f"{scope}: link {link_id} missing target node {target_id}")
            inputs = target.get("inputs") or []
            if not isinstance(target_slot, int) or target_slot < 0 or target_slot >= len(inputs):
                fail(f"{scope}: link {link_id} invalid target slot {target_id}:{target_slot}")


def main():
    if len(sys.argv) != 2:
        fail("usage: validate_meetmap_workflow.py WORKFLOW.json")

    path = Path(sys.argv[1])
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"cannot parse {path}: {exc}")

    nodes = workflow.get("nodes") or []
    links = workflow.get("links") or []
    ids = [node.get("id") for node in nodes]
    if len(ids) != len(set(ids)):
        fail("duplicate top-level node IDs")
    validate_node_links(nodes, links, "top-level")

    required_top = {
        "MeetMapGoogleDriveLatestVideo",
        "MeetMapSCAILReferenceBatch",
        "MeetMapSCAILLongVideoPlanner",
        "MeetMapSCAILChunkStitch",
        "MeetMapReleaseVRAMThenPassAudio",
        "MeetMapCreatorVoiceReference",
        "MeetMapSeedVCWithFallback",
        "MeetMapSafeTrimAudio",
        "MeetMapGoogleDriveFinalizeSafe",
        "MeetMapSafeSaveVideo",
        "MeetMapStatusCollector",
    }
    top_types = {node.get("type") for node in nodes}
    missing = sorted(required_top - top_types)
    if missing:
        fail("missing required top-level nodes: " + ", ".join(missing))
    if "SeedVCRun" in top_types:
        fail("raw SeedVCRun is still present; fallback wrapper must be used")
    if "SaveVideo" in top_types:
        fail("raw SaveVideo is still present; multi-format safe saver must be used")
    if "TrimAudioDuration" in top_types:
        fail("raw TrimAudioDuration is still present; fail-soft audio trim must be used")
    if "MeetMapGoogleDriveMarkProcessed" in top_types:
        fail("hard Drive finalizer is still present; fail-soft finalizer must be used")

    collector = next((node for node in nodes if node.get("type") == "MeetMapStatusCollector"), None)
    if collector is None:
        fail("final MeetMapStatusCollector is missing")
    collector_inputs = {item.get("name") for item in (collector.get("inputs") or [])}
    required_status_inputs = {
        "drive", "references", "chunk_plan", "stitch", "vram",
        "voice_reference", "voice_conversion", "save", "drive_finalize", "source_audio",
    }
    missing_status = sorted(required_status_inputs - collector_inputs)
    if missing_status:
        fail("status collector missing inputs: " + ", ".join(missing_status))

    node36 = next((node for node in nodes if node.get("id") == 36), None)
    if not node36 or node36.get("type") != "MeetMapSeedVCWithFallback":
        fail("node 36 must be MeetMapSeedVCWithFallback")
    voice_inputs = {item.get("name") for item in (node36.get("inputs") or [])}
    for required in ("retry_once", "fallback_to_source_audio"):
        if required not in voice_inputs:
            fail(f"Seed-VC recovery input missing: {required}")

    subgraphs = ((workflow.get("definitions") or {}).get("subgraphs") or [])
    scail = next((graph for graph in subgraphs if graph.get("id") == SCAIL_SUBGRAPH_ID), None)
    if scail is None:
        fail("SCAIL subgraph missing")

    sg_nodes = scail.get("nodes") or []
    sg_links = scail.get("links") or []
    sg_ids = [node.get("id") for node in sg_nodes]
    if len(sg_ids) != len(set(sg_ids)):
        fail("duplicate SCAIL subgraph node IDs")
    validate_node_links(sg_nodes, sg_links, "SCAIL subgraph")

    optional_loras = [
        node for node in sg_nodes
        if node.get("type") == "MeetMapOptionalLoraModelLoader"
    ]
    if len(optional_loras) < 3:
        fail(f"expected at least 3 optional LoRA loaders, found {len(optional_loras)}")
    if any(node.get("type") == "LoraLoaderModelOnly" and node.get("id") in {322, 363, 364}
           for node in sg_nodes):
        fail("one of DPO/relight/distill still uses a hard validating LoRA dropdown")

    outer_scail = next((node for node in nodes if node.get("id") == 20), None)
    if outer_scail is None:
        fail("outer SCAIL node 20 missing")
    outer_inputs = {item.get("name"): item for item in (outer_scail.get("inputs") or [])}
    for name in ("lora_name", "lora_name_1", "lora_name_2"):
        item = outer_inputs.get(name)
        if item is None:
            fail(f"outer SCAIL input missing: {name}")
        if item.get("type") != "STRING":
            fail(f"outer SCAIL LoRA input {name} must be STRING, got {item.get('type')}")

    serialized = path.read_text(encoding="utf-8")
    forbidden = [
        '"type": "SeedVCRun"',
        '"type": "LoraLoaderModelOnly",\n              "pos": [\n                300,\n                3590',
        '"scail2_relighting_lora_bf16.safetensors",\n        1',
    ]
    for token in forbidden:
        if token in serialized:
            fail(f"legacy unsafe workflow token still present: {token[:50]!r}")

    print(
        "MeetMap workflow validation passed: "
        f"{len(nodes)} top-level nodes, {len(links)} top-level links, "
        f"{len(sg_nodes)} SCAIL nodes, {len(sg_links)} SCAIL links, "
        f"{len(optional_loras)} optional LoRA loaders."
    )


if __name__ == "__main__":
    main()
