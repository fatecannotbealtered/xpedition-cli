---
id: designer-pin-types-and-erc
title: Pin types and the drc-BI-POWER and drc-BI-GROUND checks
kind: rule
applies_to: [part create, schematic check, schematic create]
tags: [designer, erc, pin-types, verification]
severity: warning
check: run schematic check and look for drc-BI-POWER and drc-BI-GROUND findings
---

schematic check runs Designer's verification (Full Verification) with the rules of the project's `Verify.ini`. It raises `drc-BI-POWER` or `drc-BI-GROUND` for every pin of type `BI` on a net that also has a pin of type `POWER` or `GROUND`. Resistors and capacitors are exempt. Pins of type `ANALOG` pass.

Rule: give the pins of other passive and discrete parts the type `ANALOG`. In the live runs these pins were typed `ANALOG`: inductor, diode, LED, switch, battery, thermistor, MOSFET drain and source, and test point. Resistors and capacitors kept `BI`.

Before this, schematic check found 9 such findings in a demo design, on battery, thermistor, diode and MOSFET pins. With those pins typed `ANALOG`, the same design verified with no finding.

Set the pin types when you create the part with part create. A part already in the library is not replaced by part create: see `designer-symbol-cache`.
