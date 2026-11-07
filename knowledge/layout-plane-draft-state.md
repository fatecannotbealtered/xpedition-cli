---
id: layout-plane-draft-state
title: A new plane shape is in Draft and connects nothing
kind: troubleshooting
applies_to: [plane create, router run, pcb check, doc save]
tags: [layout, plane, drc]
---

## Symptom

pcb check reports `PartialNets` on a plane net, such as GND, and `Dangling` vias on it, although the plane shape is there.

## Cause

A plane shape made through automation starts in the Draft state. A Draft plane generates no copper and connects nothing: every via to it dangles, and its net stays partial. Switching the plane to Dynamic (`PlaneDataState = 1`) generates the copper at once. Vias the router adds later tie in only after the plane is switched to Draft and back to Dynamic.

In one live run the GND plane was back in Draft after its pour had been saved, and the router regenerated only Dynamic planes.

plane create switches a new pour to Dynamic. router run regenerates the planes after routing: each Dynamic plane goes to Draft and back, and each plane it finds in Draft goes to Dynamic.

## What to do

- Read `generated` in the result of plane create. False means that no copper was generated.
- After router run, run pcb check. `PartialNets` and `Dangling` on the plane's net point to a plane in Draft, or to one not regenerated after routing. Tell the person: the plane must be switched to Dynamic, or to Draft and back, in Layout.
- `PartialNets` also come from pins that are simply not routed yet ("Unrouted Pin"). Read the hazard descriptions before you conclude.
