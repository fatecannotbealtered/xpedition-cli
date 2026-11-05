# Xpedition Capability Survey

English | [中文](APPLICATION_zh.md)

This survey records what Xpedition can do and how it can be automated. It is filled in before commands are designed: every domain command of xpedition-cli names the capability IDs it implements, and every capability has a disposition.

## Application

- Name: Xpedition
- Vendor: {{fill: the vendor of Xpedition}}
- Versions surveyed: {{fill: the Xpedition versions this survey was made on; xpedition-cli supports 2604}}

The operating systems and CPU architectures on which Xpedition runs, or in device mode the device driver or SDK; the platform packages of xpedition-cli follow from this table:

| Operating system | CPU architectures |
|---|---|
| Windows | x64 |

{{fill: check the table: keep only the rows of the systems on which Xpedition (in device mode, the device driver or SDK) runs, and add arm64 where it also runs on arm64; systems are Windows, macOS or Linux, architectures x64 or arm64, comma-separated; then replace this line with the source of these facts}}

## Automation Interfaces

Every interface Xpedition offers, and where code that calls it must run. An interface the CLI can call is used from the adapter layer in `xpedition_cli/adapter/`. An interface that works only inside the process of Xpedition or on its main thread needs a host component in `plugin/`.

| Interface | Called from | Notes |
|---|---|---|
| {{fill: an interface, such as a Python API, COM, the command line of Xpedition, a file format, a plugin SDK or a script language}} | {{fill: the CLI, or only inside the process or on the main thread}} | {{fill: version limits, licensing or stability notes}} |

## Object Model

{{fill: the objects of Xpedition, from documents down to the objects inside them, with their names in the user interface and the manual; these names become command nouns}}

## Units and Coordinates

{{fill: the internal units and the display units, the origin and axis directions of each coordinate system, and how angles are measured}}

## Threading and Blocking

{{fill: the main-thread rules, how Xpedition shows that it is busy, and the dialogs that block automation, such as license, save, error and update dialogs}}

## Read-back

{{fill: for each kind of change, how xpedition-cli can read it back and how exactly: rounding, snapping to a grid, tolerances}}

## Save and Undo

{{fill: how saving works, how unsaved changes show, whether save-as exists, and whether undo exists and through which interface}}

## Instances and Concurrency

{{fill: whether several instances can run at once, how to tell them apart, and what happens when a person works in Xpedition at the same time}}

## Licensing

{{fill: the license types, which license automation needs, and the license dialogs Xpedition shows}}

## Capabilities

Each capability has one row with an ID `CAP-nnn`, unique in this survey, and one disposition: `provided` (a domain command offers it), `deferred` (worth offering, left for a later version), `excluded` (will not be offered, for example because it is unsafe or cannot be automated reliably) or `unknown` (not yet known whether Xpedition allows it). Every row names the interface that reaches the capability; a row other than `provided` gives the reason in one line.

| ID | Capability | Interface | Disposition | Reason |
|---|---|---|---|---|
| CAP-001 | {{fill: a capability of Xpedition}} | {{fill: the interface that reaches it}} | unknown | {{fill: why, or - when provided}} |

## Risks

Each risk or limit found in the survey becomes an entry in `knowledge/`, or gives here the reason it is left out.

{{fill: the risks and limits found in the survey, one list item each, with the knowledge entry it became or the reason it has none}}
