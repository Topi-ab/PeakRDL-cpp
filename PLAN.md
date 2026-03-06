# peakrdl-cpp Plan And Status

This document reflects the current implemented behavior and the remaining open
design work.

## 1. Implemented Status

### 1.1 Core API shape

1. Hierarchical access is generated with arrays and nested blocks.
2. Field API shape:
   - `field.read()`
   - `field.write(value)` when writable
   - `field.shadow.read()`
   - `field.shadow.write(value)` when writable
3. Scope shadow APIs exist at register, regfile, and addrmap scopes:
   - `.shadow.read_hw()`
   - `.shadow.flush()`

### 1.2 Access rules and field capabilities

1. `sw=r` fields do not generate `write()`.
2. `sw=w` fields do not generate `read()`.
3. `sw=rw` fields generate both read and write APIs.
4. `onwrite` fields currently do not generate software write APIs.
   - This avoids incorrect behavior for side-effect modes that are not yet modeled.

### 1.3 Shadow and read/write behavior

1. `field.write(value)` is direct hardware write logic using register context.
2. `field.shadow.write(value)` updates shadow state only.
3. `field.read()` performs a hardware read and updates register shadow state.
4. `field.shadow.read()` reads shadow state.
5. Dirty flag is tracked per register.
6. `shadow.flush()` writes only dirty registers with supported shadow writes.
7. `singlepulse` fields are auto-cleared in shadow after flush.
8. `onread=rclr/rset` behavior is modeled in shadow update logic.

### 1.4 Types, widths, and addressing

1. `addr_t` is fixed to `std::uint32_t`.
2. `data_t` is deduced from RDL `accesswidth` (8/16/32/64 only).
3. Mixed numeric `accesswidth` values are generation-time hard errors.
4. `regwidth <= accesswidth` is enforced.
5. Generation-time Python checks enforce that computed offsets fit `addr_t`.
   - Overflow/truncation is rejected during generation.

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
   - `shadow`
   - `ShadowOps`
   - `shadow_read_hw_impl`
   - `shadow_flush_impl`
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

1. Finalize whether `field.shadow.read()` should return read-shadow or write-shadow.
2. Define first-read shadow validity policy explicitly in docs/API contract.
3. Decide whether to expose both read-shadow and write-shadow views.

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
2. Freeze shadow-read semantics (`read-shadow` vs `write-shadow` contract).
3. Decide array bounds behavior.
4. Improve conflict diagnostics aggregation.
