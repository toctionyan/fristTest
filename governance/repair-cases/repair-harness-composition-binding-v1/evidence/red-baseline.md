# Red baseline: Harness composition binding

Date: 2026-08-23

The portable Authoring Core accepts Skill extension metadata, but it has no
composition declaration, deterministic binding algorithm, graph-edge insertion,
provenance manifest, or `skillctl.py authoring compose` command. A user must
rewrite the original Workflow to add a compatible Skill, so extension points are
descriptive only and cannot yet provide reusable overlays.

