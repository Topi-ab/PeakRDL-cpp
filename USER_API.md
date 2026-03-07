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
   - Staged value used for deferred writes (`wr_shadow.flush()` / `wr_shadow.flush_always()`).

Dirty is tracked per register write-shadow state:

- `dirty == true`: staged write-shadow has pending changes.
- `dirty == false`: no staged write pending.

Important:

- `rd_shadow` APIs expose read-shadow.
- `wr_shadow` APIs expose/write write-shadow.

## 2. Field API

Generated per-field APIs (depending on `sw` access):

- `field.read()`
- `field.write(value)` (if writable)
- `field.rd_shadow.read()`
- `field.wr_shadow.read()`
- `field.wr_shadow.write(value)` (if writable)

### 2.1 `field.read()`

- Performs hardware read of the whole register.
- Updates register read-shadow for readable bits.
- Updates register write-shadow for readable bits.
- Returns this field's bit slice.

### 2.2 `field.write(value)` (direct write)

- Performs immediate hardware write behavior (not deferred).
- Updates register write-shadow for affected bits.
- Updates register read-shadow for affected bits.
- Clears register dirty after direct write path.

### 2.3 `field.rd_shadow.read()`

- Reads this field from register read-shadow.

### 2.4 `field.wr_shadow.read()`

- Reads this field from register write-shadow.

### 2.5 `field.wr_shadow.write(value)` (staged write)

- Updates this field bits in register write-shadow.
- Marks register dirty if staged value changed.
- Does not write hardware immediately.
- Does not modify read-shadow.

## 3. Scope Shadow API

Generated on register, regfile, and addrmap scopes:

- `.rd_shadow.read_hw()`
- `.wr_shadow.flush()`
- `.wr_shadow.flush_always()`

At register scope:

- `.wr_shadow.dirty()`

### 3.1 `reg.rd_shadow.read_hw()`

- Reads hardware for that register.
- Refreshes register read-shadow from hardware-read path (readable bits).
- Refreshes register write-shadow for readable bits from hardware-read path.

### 3.2 `reg.wr_shadow.flush()`

- If register dirty and shadow write is supported:
  - writes register write-shadow to hardware
  - clears dirty
- Does not perform an additional hardware read to refresh read-shadow.

### 3.3 `reg.wr_shadow.flush_always()`

- Performs unconditional writeback (ignores dirty-state gating).
- Side effect: flushed register is marked clean afterward.

### 3.4 `regfile/addrmap rd/wr shadow`

- `regfile.rd_shadow.read_hw()` / `addrmap.rd_shadow.read_hw()`
  - recurse over child nodes and refresh register shadows through HW reads.
- `regfile.wr_shadow.flush()` / `addrmap.wr_shadow.flush()`
  - flush dirty child registers.
- `regfile.wr_shadow.flush_always()` / `addrmap.wr_shadow.flush_always()`
  - unconditionally flush all flush-capable child registers.

## 4. Dirty Semantics

Per-register dirty is set/cleared as follows:

- Set to `true` when `field.wr_shadow.write(...)` changes write-shadow.
- Cleared by `reg.wr_shadow.flush()` after a successful staged writeback.
- Cleared by `reg.wr_shadow.flush_always()` after unconditional writeback.
- Cleared by direct `field.write(...)` path.

`dirty` means staged value changed since last successful direct/flush write.
It does not claim equality/inequality versus externally modified hardware.

## 5. Access Style Summary

- Immediate hardware access:
  - `field.read()`
  - `field.write(value)`
- Staged access:
  - `field.wr_shadow.write(value)` then `...wr_shadow.flush()` or `...wr_shadow.flush_always()`
- Cached observation:
  - `field.rd_shadow.read()`
  - `...rd_shadow.read_hw()`

## 6. Notes

- `sw=r` fields do not generate `write()` / `wr_shadow.write()`.
- `sw=w` fields do not generate `read()`.
- Signed and unsigned integral inputs are both accepted for `write()` and `wr_shadow.write()`.
- In `exceptions` mode, errors throw `std::runtime_error`.
- In `status` mode, errors are reported via `ok()/last_error()/clear_error()`.
