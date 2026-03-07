# peakrdl-cpp Plan And Status

This document reflects the current implemented behavior and the remaining open
design work.

## 1. Implemented Status

### 1.1 Core API shape

1. Hierarchical access is generated with arrays and nested blocks.
2. Field API shape:
   - `field.read()`
   - `field.write(value)` when writable
   - `field.rd_shadow.read()`
   - `field.wr_shadow.read()`
   - `field.wr_shadow.write(value)` when writable
3. Scope shadow APIs exist at register, regfile, and addrmap scopes:
   - `.rd_shadow.read_hw()`
   - `.wr_shadow.flush()`
   - `.wr_shadow.flush_always()`

### 1.2 Access rules and field capabilities

1. `sw=r` fields do not generate `write()`.
2. `sw=w` fields do not generate `read()`.
3. `sw=rw` fields generate both read and write APIs.
4. `onwrite` fields currently do not generate software write APIs.
   - This avoids incorrect behavior for side-effect modes that are not yet modeled.

### 1.3 Shadow and read/write behavior

1. `field.write(value)` is direct hardware write logic using register context.
   - For mixed RW+WO registers, direct write composition preserves WO bits from
     shadow while merging readable bits from hardware read.
2. `field.wr_shadow.write(value)` updates register write-shadow only.
3. `field.read()` performs a hardware read and updates register read-shadow and write-shadow for readable bits.
4. `field.rd_shadow.read()` reads register read-shadow.
5. `field.wr_shadow.read()` reads register write-shadow.
6. Dirty flag is tracked per register.
7. `wr_shadow.flush()` writes only dirty registers with supported shadow writes.
8. `wr_shadow.flush_always()` writes supported shadow-write registers regardless of dirty state.
9. Flush operations mark flushed registers clean.
10. `singlepulse` fields are auto-cleared in shadow after flush.
11. `onread=rclr/rset` behavior is modeled in shadow update logic.

### 1.4 Types, widths, and addressing

1. `addr_t` is fixed to `std::uint32_t`.
2. `data_t` is deduced from RDL `accesswidth` (8/16/32/64 only).
3. Mixed numeric `accesswidth` values are generation-time hard errors.
4. `regwidth <= accesswidth` is enforced.
5. Generation-time Python checks enforce that computed offsets fit `addr_t`.
   - Overflow/truncation is rejected during generation.
6. Register access span overflow is checked at generation time.
   - `start + (accesswidth/8) - 1` must fit `addr_t`.

### 1.5 Error handling and validation

1. Two error styles are supported:
   - `exceptions`
   - `status` (`ok()`, `last_error()`, `clear_error()`)
2. Write range validation is enabled by default.
3. Range validation can be disabled at generation time:
   - Python API: `check_write_range=False`
   - CLI: `--no-write-range-check`
4. Signed/unsigned write dispatch is compile-time template-based:
   - integral types only
   - `bool` rejected
   - signed and unsigned paths selected with `if constexpr`

### 1.6 Naming conflict policy

1. Hard generation errors are used for reserved API symbol conflicts.
2. Reserved names include API helpers such as:
   - `rd_shadow`
   - `wr_shadow`
   - `RdShadowOps`
   - `WrShadowOps`
   - `rd_shadow_read_hw_impl`
   - `wr_shadow_flush_impl`
   - top/register helper symbols (`ok`, `last_error`, `read`, etc.)

### 1.7 Packaging and developer flow

1. Project is pip-installable from source.
2. PeakRDL plugin entry point is registered (`peakrdl cpp`).
3. Python API is exposed as `peakrdl_cpp.CppExporter`.
4. License file is present and referenced from package metadata (LGPL-3.0).
5. Tests cover:
   - generation failures and diagnostics
   - compile/link/run behavior for generated C++
   - example in-place generation
6. Fixture-driven tests auto-discover `tests/fixtures/rdl/**/*.rdl`.
7. Test extra includes parallel test support (`pytest-xdist`).

## 2. Current Constraints

1. Array support is one-dimensional only.
2. `swwe` and `swwel` are unsupported.
3. Interrupt field semantics are unsupported.
4. `onread=ruser` is unsupported.
5. `onwrite` side-effect transforms are not implemented.
6. Single-thread model only.

## 3. Open Design Items

### 3.1 `onwrite` side-effect semantics

1. Decide supported subset (`w1c`, `w1s`, `w1t`, `w0c`, `w0s`, `w0t`).
2. Define exact transform rules for direct write and shadow write behavior.
3. Re-enable write APIs only when semantics are fully defined.

### 3.2 Shadow model clarity

1. `rd_shadow` and `wr_shadow` split is implemented.
2. Define first-read shadow validity policy explicitly in docs/API contract.

### 3.3 Array bounds policy

1. Decide bounds strategy for `operator[]`:
   - unchecked (current)
   - checked API (`at()`)
   - optional debug checks

### 3.4 Conflict diagnostics UX

1. Decide whether to report first conflict only or aggregate all conflicts.
2. Standardize diagnostic text format across conflict classes.

### 3.5 Compile-time optimization pass

1. Increase compile-time validation/constexpr usage where safe.
2. Keep runtime checks only for value-dependent behavior.
3. Re-evaluate generated code size/performance after changes.

### 3.6 External coherency policy

1. Define behavior for hardware updates occurring outside this API.
2. Decide whether explicit invalidate/sync APIs are needed.

## 4. Next Priorities

1. Finalize `onwrite` support policy and implementation scope.
2. Decide array bounds behavior.
3. Improve conflict diagnostics aggregation.
