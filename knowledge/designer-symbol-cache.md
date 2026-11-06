---
id: designer-symbol-cache
title: Designer keeps a symbol it has placed; give a changed symbol a new name
kind: guide
applies_to: [part create, part get, schematic create]
tags: [designer, symbols, cache, library]
---

Designer keeps the definition of every symbol it has placed in the open project. If a symbol file is written again under the same name, Designer ignores the new file and keeps drawing the old symbol.

Symbol files are text files, `SymbolLibs/<partition>/sym/<name>.<version>`. The highest version is the current symbol. xpedition-cli 1.0.3 wrote a replaced symbol as the next version, and Designer took it; the old file stayed.

In this release, part create skips a part identical to the library's and refuses a part number the library holds with other content. So it does not replace a symbol.

What to do:

- To change a part's symbol, create a new part with a new part number and a new symbol name.
- Then draw again with schematic create, using the new part number.
- The records do not say whether closing and reopening the project clears this cache. Do not rely on it.
