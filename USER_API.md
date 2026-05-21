# PeakRDL-cpp User API Guide

This document describes the generated C++ user API, with emphasis on:

- bus adapter requirements
- `dirty` behavior
- `read-shadow`
- `write-shadow`

## 1. Bus Adapter Requirements

Generated register-map classes are templated on a user-provided bus adapter type:

```cpp
demo::MyRoot<MyBus> root(bus, base_address);
```

The adapter must provide these methods:

- `data_t read(addr_t addr);`
- `void write(addr_t addr, data_t value);`

`addr_t` is currently generated as `std::uint32_t`. `data_t` is generated from the
SystemRDL `accesswidth` (`std::uint8_t`, `std::uint16_t`, `std::uint32_t`, or
`std::uint64_t`).

Example:

```cpp
struct MyBus {
    demo::data_t read(demo::addr_t addr) {
        return read_register_word(addr);
    }

    void write(demo::addr_t addr, demo::data_t value) {
        write_register_word(addr, value);
    }
};
```

The generated code calls these methods for all hardware reads and writes. This
contract is currently documented but not yet enforced via C++ concepts/static
constraints.

## 2. Mental Model

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

## 3. Field API

Generated per-field APIs (depending on `sw` access):

- `field.read()`
- `field.write(value)` (if writable)
- `field.rd_shadow.read()`
- `field.wr_shadow.read()`
- `field.wr_shadow.write(value)` (if writable)

For ordinary scalar fields, reads return `data_t`, and writes accept signed or
unsigned integral input types.

For fields wider than `data_t`, `value` is a compact little-endian
`std::array<data_t, N>`:

- element 0 holds the least-significant field bits
- the final element uses only the remaining valid high bits
- unused high bits in the final element are rejected when write range checks are enabled

For fields with a SystemRDL `encode = enum_type;` property, the generated C++ API
uses the corresponding generated enum type instead of raw integral values.

### 3.1 `field.read()`

- Performs hardware read of the whole register.
- Updates register read-shadow for readable bits.
- Updates register write-shadow for readable bits.
- Returns this field's bit slice.

### 3.2 `field.write(value)` (direct write)

- Performs immediate hardware write behavior (not deferred).
- Updates register write-shadow for affected bits.
- Updates register read-shadow for affected bits.
- Clears register dirty after direct write path.

### 3.3 `field.rd_shadow.read()`

- Reads this field from register read-shadow.

### 3.4 `field.wr_shadow.read()`

- Reads this field from register write-shadow.

### 3.5 `field.wr_shadow.write(value)` (staged write)

- Updates this field bits in register write-shadow.
- Marks register dirty if staged value changed.
- Does not write hardware immediately.
- Does not modify read-shadow.

### 3.6 Enum Fields

SystemRDL enums used with field `encode` are emitted as public C++ enum classes
in the generated namespace.

Example RDL:

```systemrdl
enum mode_e {
    idle = 0;
    run = 1;
    sleep = 2;
    fault = 3;
};

addrmap top {
    reg {
        field {
            sw = rw;
            encode = mode_e;
        } mode[1:0] = 0;
    } control @0x0;
};
```

Generated API shape:

```cpp
enum class mode_e : data_t {
    idle = static_cast<data_t>(0ull),
    run = static_cast<data_t>(1ull),
    sleep = static_cast<data_t>(2ull),
    fault = static_cast<data_t>(3ull)
};

demo::mode_e current = root.control.mode.read();
root.control.mode.write(demo::mode_e::run);
root.control.mode.wr_shadow.write(demo::mode_e::sleep);
demo::mode_e staged = root.control.mode.wr_shadow.read();
```

For encoded scalar fields:

- `field.read()` returns the generated enum type.
- `field.write(...)` accepts the generated enum type.
- `field.rd_shadow.read()` returns the generated enum type.
- `field.wr_shadow.read()` returns the generated enum type.
- `field.wr_shadow.write(...)` accepts the generated enum type.

Raw integral writes are intentionally not part of the encoded field API.

## 4. Scope Shadow API

Generated on register, regfile, and addrmap scopes:

- `.rd_shadow.read_hw()`
- `.wr_shadow.flush()`
- `.wr_shadow.flush_always()`

At register scope:

- `.wr_shadow.dirty()`

### 4.1 `reg.rd_shadow.read_hw()`

- Reads hardware for that register.
- Refreshes register read-shadow from hardware-read path (readable bits).
- Refreshes register write-shadow for readable bits from hardware-read path.

### 4.2 `reg.wr_shadow.flush()`

- If register dirty and shadow write is supported:
  - writes register write-shadow to hardware
  - clears dirty
- Does not perform an additional hardware read to refresh read-shadow.

### 4.3 `reg.wr_shadow.flush_always()`

- Performs unconditional writeback (ignores dirty-state gating).
- Side effect: flushed register is marked clean afterward.

### 4.4 `regfile/addrmap rd/wr shadow`

- `regfile.rd_shadow.read_hw()` / `addrmap.rd_shadow.read_hw()`
  - recurse over child nodes and refresh register shadows through HW reads.
- `regfile.wr_shadow.flush()` / `addrmap.wr_shadow.flush()`
  - flush dirty child registers.
- `regfile.wr_shadow.flush_always()` / `addrmap.wr_shadow.flush_always()`
  - unconditionally flush all flush-capable child registers.

## 5. Dirty Semantics

Per-register dirty is set/cleared as follows:

- Set to `true` when `field.wr_shadow.write(...)` changes write-shadow.
- Cleared by `reg.wr_shadow.flush()` after a successful staged writeback.
- Cleared by `reg.wr_shadow.flush_always()` after unconditional writeback.
- Cleared by direct `field.write(...)` path.

`dirty` means staged value changed since last successful direct/flush write.
It does not claim equality/inequality versus externally modified hardware.

## 6. Access Style Summary

- Immediate hardware access:
  - `field.read()`
  - `field.write(value)`
- Staged access:
  - `field.wr_shadow.write(value)` then `...wr_shadow.flush()` or `...wr_shadow.flush_always()`
- Cached observation:
  - `field.rd_shadow.read()`
  - `...rd_shadow.read_hw()`

## 7. Notes

- `data_t` is the bus access word type and is derived from SystemRDL `accesswidth`.
- Registers may be wider than `data_t` up to 64 bits. The generated runtime stores register shadows as arrays of `data_t` words.
- Multiword readable registers require `buffer_reads=true`; multiword writable registers require `buffer_writes=true`. If those UDPs are not visible to the SystemRDL compiler, generation fails closed when they are needed.

- `sw=r` fields do not generate `write()` / `wr_shadow.write()`.
- `sw=w` fields do not generate `read()`.
- Non-enum scalar fields accept signed and unsigned integral inputs for `write()` and `wr_shadow.write()`.
- Enum scalar fields use their generated enum type for reads and writes.
- In `exceptions` mode, errors throw `std::runtime_error`.
- In `status` mode, errors are reported via `ok()/last_error()/clear_error()`.
