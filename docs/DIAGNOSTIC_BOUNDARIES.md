# Diagnostic backend boundaries

Two narrow fixes based on `main@d42b226`. This PR is independent of PRs #4 and #5;
neither existing branch is changed or merged.

`agent capabilities` now returns capability metadata before any project load or
backend requirement check, matching the existing streaming capability method.
This does not claim the configured native adapter is healthy or licensed. The
registry may still inspect local capability configuration. Use the appropriate
runtime diagnostics before operating on a real project.

`analysis run` is declared MockBackend-only in the existing reference. Previously,
selecting NativeBackend loaded a native snapshot and then executed the Mock
analysis function (the result did label its engine `mock`). The explicit native
combination now fails with non-retryable `E_BACKEND_UNAVAILABLE` before reading a
project. This is an intentional compatibility tightening, not a new native engine.

Native stored analysis reads (`analysis results/erc/drc/dfm`) are not disabled.
The native `pcb drc` and `review run` entry points remain separate capabilities;
they are not interchangeable with every analysis kind, and this patch does not
claim a native DFM runner exists.

The regression tests exercise the public CLI boundary, fail if unsupported
analysis touches either backend access or the mock analysis function, and ensure
capability discovery ignores invalid project contents. Stored native reads use
a fake adapter to protect their existing dispatch path. No licensed Xpedition
installation or production project is used. The branch workflow records full-suite
results in `DIAGNOSTIC_BOUNDARIES_VALIDATION.json` only after all checks pass.
