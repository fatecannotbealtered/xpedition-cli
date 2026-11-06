---
id: designer-chinese-text-in-pdf
title: Chinese text comes out garbled in the schematic PDF
kind: troubleshooting
applies_to: [schematic export, schematic create, part create, part get]
tags: [pdf, encoding, chinese, sch2pdf]
---

## Symptom

Titles and notes in Chinese look right in Designer. In the PDF made by schematic export they show as strings of Latin characters.

## Cause

Designer accepts Unicode text and draws Chinese correctly on screen. The project stores the text in the system code page (GBK on the measured machine). `sch2pdf`, which schematic export runs, writes those bytes as Latin-1 glyphs.

The text can be recovered: encode the garbled string as Latin-1, then decode the bytes as GBK. A review that extracts notes from the PDF must do this.

## What to do

- When people must read the PDF, use English for titles and notes, or tell the person that Chinese text will be garbled in it.
- Text that the system code page cannot hold cannot be stored. For library texts, xpedition-cli 1.0.3 refused such characters in part descriptions and properties.
- Before 1.0.3 fixed it, Chinese text reached the central library as question marks while the read-back still passed. After part create, check Chinese descriptions with part get.
- `sch2pdf` prints only what lies inside each sheet's border. Objects outside the border do not appear in the PDF.
