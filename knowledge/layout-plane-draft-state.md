---
id: layout-plane-draft-state
title: A new plane shape is in Draft and connects nothing
kind: troubleshooting
applies_to: [plane create, router run, pcb check, doc save]
tags: [layout, plane, drc]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/COMPATIBILITY.md (Batch DRC and hazards) and docs/E2E.md (Recorded run, checked; the board on KiCad footprints, 2026-09-14; selected placement, 2026-09-19) of npm @fateforge/xpedition-cli@1.0.3; old implementation (commit 4a054a3), CHANGELOG.md
updated_on: 2026-11-05
---

## Symptom

pcb check reports `PartialNets` on a plane net, such as GND, and `Dangling` vias on it, although the plane shape is there.

## Cause

A plane shape made through automation starts in the Draft state. A Draft plane generates no copper and connects nothing: every via to it dangles, and its net stays partial. Switching the plane to Dynamic (`PlaneDataState = 1`) generates the copper at once. Vias the router adds later tie in only after the plane is switched to Draft and back to Dynamic.

In one live run the GND plane was back in Draft after its pour had been saved, and the router regenerated only Dynamic planes.

xpedition-cli 1.0.3 switched a new pour to Dynamic. Its routing step later regenerated planes it found in Draft as well.

## What to do

- Read `generated` in the result of plane create. False means that no copper was generated.
- After router run, run pcb check. `PartialNets` and `Dangling` on the plane's net point to a plane in Draft, or to one not regenerated after routing. Tell the person: the plane must be switched to Dynamic, or to Draft and back, in Layout.
- `PartialNets` also come from pins that are simply not routed yet ("Unrouted Pin"). Read the hazard descriptions before you conclude.
