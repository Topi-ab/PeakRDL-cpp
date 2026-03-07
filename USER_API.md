# PeakRDL-cpp User API Guide

This document describes the generated C++ user API, with emphasis on:

- `dirty` behavior
- `read-shadow`
- `write-shadow`

## 1. Mental Model

Each generated register keeps two software shadow values:

1. `read-shadow`
   - Cache of what was observed from hardware reads.
2. `write-shadow`
   - Staged value used for deferred writes (`shadow.flush()` / `shadow.flush_always()`).

Dirty is tracked per register:

- `dirty == true`: register `write-shadow` has pending staged changes.
- `dirty == false`: no staged change waiting to be flushed.

Important: `read-shadow` and `write-shadow` are register-level values.
Field APIs operate on the field bit slice inside register `read-shadow` and `write-shadow`.

## 2. Field API

Generated per-field APIs (depending on `sw` access):

- `field.read()`
- `field.write(value)` (if writable)
- `field.shadow.read()`
- `field.shadow.write(value)` (if writable)

### 2.1 `field.read()`

- Performs hardware read of the whole register.
- Updates register `read-shadow` for readable bits.
- Updates register `write-shadow` for readable bits.
- Returns this field's bit slice.

### 2.2 `field.write(value)` (direct write)

- Performs immediate hardware write behavior (not deferred).
- Updates register `write-shadow` for affected bits.
- Updates register `read-shadow` for affected bits.
- Clears register dirty after direct write path.

### 2.3 `field.shadow.write(value)` (staged write)

- Updates this field bits in register `write-shadow`.
- Marks register dirty if staged value changed.
- Does not write hardware immediately.

### 2.4 `field.shadow.read()`

- Reads this field from register `read-shadow`.

## 3. Scope Shadow API

Generated on register, regfile, and addrmap scopes:

- `.shadow.read_hw()`
- `.shadow.flush()`
- `.shadow.flush_always()`

### 3.1 `reg.shadow.read_hw()`

- Reads hardware for that register.
- Refreshes register `read-shadow` from hardware-read path (readable bits).
- Refreshes register `write-shadow` for readable bits from hardware-read path.

### 3.2 `reg.shadow.flush()`

- If register dirty and shadow write is supported:
  - writes register `write-shadow` to hardware
  - clears dirty
- Does not perform an additional hardware read to refresh `read-shadow`.

- `reg.shadow.flush_always()` performs unconditional writeback
  (ignores dirty-state gating).
- Side effect: flushed register is marked clean afterward.

### 3.3 `regfile.shadow.read_hw()` / `addrmap.shadow.read_hw()`

- Calls `read_hw()` over all supported child registers in scope.
- For each child register, refreshes child `read-shadow` and child `write-shadow` for readable bits.

### 3.4 `regfile.shadow.flush()` / `addrmap.shadow.flush()`

- Flushes all dirty child registers in scope.
- For each flushed child register, writes child `write-shadow` to hardware and marks that child clean.

- `regfile.shadow.flush_always()` / `addrmap.shadow.flush_always()`
  unconditionally write all flush-capable registers in scope.
- Side effect: all successfully flushed registers in scope are marked clean.

## 4. Dirty Semantics

Per-register dirty is set/cleared as follows:

- Set to `true` when `field.shadow.write(...)` changes `write-shadow`.
- Cleared by `reg.shadow.flush()` after a successful staged writeback.
- Cleared by `reg.shadow.flush_always()` after unconditional writeback.
- Direct `field.write(...)` path clears dirty for that register write path.

## 5. Access Style Summary

- Immediate hardware access:
  - `field.read()`
  - `field.write(value)`
- Staged access:
  - `field.shadow.write(value)` then `...shadow.flush()` or `...shadow.flush_always()`

## 6. Notes

- `sw=r` fields do not generate `write()`.
- `sw=w` fields do not generate `read()`.
- Signed and unsigned integral inputs are both accepted for `write()` and `shadow.write()`.
- In `exceptions` mode, errors throw `std::runtime_error`.
- In `status` mode, errors are reported via `ok()/last_error()/clear_error()`.
