# Version History

## 0.3.0

- Added SystemRDL `encode` enum support in generated C++ headers.
  - RDL enum types are emitted as public C++ `enum class` types.
  - Encoded scalar field APIs use the generated enum type for `read()`, `write(...)`, `rd_shadow.read()`, `wr_shadow.read()`, and `wr_shadow.write(...)`.
  - Raw integral writes are intentionally not part of the encoded field API.
- Documented the generated bus adapter contract in `USER_API.md`.
- Added focused runtime coverage for 1-bit `sw=rw` fields with unsigned and signed writes.

## 0.2.0

- Generated hierarchical C++ register-map APIs from SystemRDL addrmaps, regfiles, registers, fields, and one-dimensional arrays.
- Added read/write field APIs that honor SystemRDL `sw` access permissions.
- Added read-shadow and write-shadow APIs for fields, registers, regfiles, and addrmaps.
- Added configurable error handling with `exceptions` and `status` modes.
- Added optional runtime write range validation.
- Added support for registers wider than the bus `accesswidth` up to 64 bits, using compact `std::array<data_t, N>` field values where needed.
- Added hard generation errors for reserved generated API symbol conflicts and unsupported mixed `accesswidth` designs.

## 0.1.0

- Initial PyPI package release, tagged as `v0.1.0`.
- Added the PeakRDL exporter plugin entry point for `peakrdl cpp`.
- Added Python API export through `peakrdl_cpp.CppExporter`.
- Generated C++ register-map headers with hierarchical object access, register arrays, and field read/write APIs.
- Added read-shadow and write-shadow APIs for fields, registers, regfiles, and addrmaps.
- Added configurable generated error handling with `exceptions` and `status` modes.
- Added optional generated runtime write range validation.
- Added hard generation errors for reserved generated API symbol conflicts and mixed `accesswidth` designs.
- Added packaging metadata, pytest test extra, PyPI publishing workflow, release guide, and generated-output license exception.
