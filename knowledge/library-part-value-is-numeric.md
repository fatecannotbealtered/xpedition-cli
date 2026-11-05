---
id: library-part-value-is-numeric
title: A part's Value is a number with an SI multiplier
kind: rule
applies_to: [part create, part get, part list, packager run]
tags: [library, parts, value]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/COMPATIBILITY.md (The central library as text, verified 2026-09-29) and docs/E2E.md (run of 2026-09-29 and 30) of npm @fateforge/xpedition-cli@1.0.3; capability survey docs/APPLICATION.md (Read-back)
updated_on: 2026-11-05
severity: error
check: run part get after part create; value reads back as the number given (10k reads back as 10K)
---

The parts database stores a part's `Value` property only as a number with an SI multiplier. `10k` is stored as `10K`, `4.7k` as `4.7K` and `1u` as `1u`.

Any other text is stored as 0, with only a warning in the converter log (`无效的值 ... (对于特性 "Value")`). In the live run, `10k 1%`, `100nF`, `3.3V` and `TPS7A2033` were all stored as 0.

Rule: give Value only as a number with a multiplier, such as `10k`, `4.7k` or `1u`, or leave it out. Note that even `100nF` was stored as 0. Put any other text, such as a voltage, a tolerance or a part name, into the description or another property. Other properties take any text. The library's own property names include `Manufacturer Name` and `Manufacturer Part Number`.

When you compare a read-back, compare Value as a number: `10k` and `10K` are the same.

The packager does not copy `Value`, `Part Number` and the other properties checked off in Library Manager's Property Definition Editor from the parts database to the symbols. Its log lists them. That is not an error.
