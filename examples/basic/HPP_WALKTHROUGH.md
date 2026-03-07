# `demo_regs.hpp` Walkthrough

This document explains the currently generated header:

- `examples/basic/demo_regs.hpp`

Generated from:

- `examples/basic/design.rdl`

## 1. Preamble And Global Types

At the top of the generated file:

- Generated-file header with `SPDX-License-Identifier: CC0-1.0`
- `namespace demo`
- `using addr_t = std::uint32_t;`
- `using data_t = std::uint32_t;`
- `kAccessWidth = 32`
- `kCheckWriteRange = true`

These are generation-time results from the RDL and exporter options.

## 2. `detail` Runtime Layer

The `detail` namespace contains reusable runtime primitives.

### 2.1 `detail::Context`

Holds:

- bus pointer
- base address
- error state (`ok()`, `last_error()`, `clear_error()`)

`fail()` throws in exception mode or stores status in status mode.

### 2.2 `detail::NodeArray`

Wrapper for RDL arrays.

- `operator[]`
- `size()`
- `set()`

Debug assertions check index bounds and non-null entries.

### 2.3 `detail::RegisterState`

This is the core register engine. It owns:

- register address computation
- HW read path
- direct write path (`field.write()`)
- staged write path (`field.shadow.write()`)
- shadow operations (`read_hw`, `flush`, `flush_always`)
- dirty tracking
- side-effect behavior (`rclr`, `rset`, `singlepulse`)

Key methods:

- `read_hw()`
- `flush()`
- `flush_always()`
- `read_field_hw(mask, lsb)`
- `read_field_shadow(mask, lsb)`
- `direct_write_unsigned/signed(...)`
- `shadow_write_unsigned/signed(...)`

## 3. Register Class

Example register class in this design:

- `MyRootRegfile1SubRegfileExampleRegReg`

Main API:

- `read()`
- `address()`
- `supports_shadow_write()`
- nested `shadow` ops:
  - `read_hw()`
  - `flush()`
  - `flush_always()`
  - `dirty()`

Field objects in this register:

- `enable`
- `mode`
- `status`

## 4. Field Classes

Each field class contains:

- constants: `LSB`, `MSB`, `WIDTH`, `MASK`, `SW`, `SINGLEPULSE`
- `read()` only if SW-readable
- `write(IntT)` only if SW-writable
- nested `shadow` object with:
  - `read()`
  - optional `write(IntT)` when writable

Note: old per-field `ONREAD` constants are no longer emitted.

### 4.1 Signed vs Unsigned Input Dispatch

In both `write(IntT)` and `shadow.write(IntT)`:

- `static_assert` requires integral and rejects `bool`
- `if constexpr (std::is_signed_v<IntT>)` selects signed path
- otherwise unsigned path

## 5. Call Graphs

The graphs use `enable` as an example.

### 5.1 `field.read()` (HW read path)

Example:
`my_root.regfile_1[i].sub_regfile.example_reg.enable.read()`

```text
Field::read()
  -> RegisterState::read_field_hw(MASK, LSB)
      -> RegisterState::read_hw()
          -> bus->read(address)
          -> RegisterState::apply_hw_read(raw)
      -> (raw & MASK) >> LSB
```

`apply_hw_read(raw)` updates register shadows for readable bits:

- updates `read-shadow`
- updates `write-shadow`
- then applies `rclr/rset` effects to `write-shadow`

### 5.2 `field.write(value)` (direct HW write path)

```text
Field::write(IntT)
  -> signed/unsigned compile-time dispatch
  -> RegisterState::direct_write_signed(...) or direct_write_unsigned(...)
      -> optional range check
      -> compose base register value
         (RW fields may read HW; WO bits are preserved from write-shadow)
      -> bus->write(address, next)
      -> update both register write-shadow and read-shadow for this field bits
      -> clear dirty
      -> singlepulse: clear corresponding shadow bits after write
```

### 5.3 `field.shadow.read()` (shadow read path)

```text
Field::ShadowOps::read()
  -> RegisterState::read_field_shadow(MASK, LSB)
      -> (read_shadow_ & MASK) >> LSB
```

Important: `field.shadow.read()` returns bits from register **read-shadow**.

### 5.4 `field.shadow.write(value)` (staged write path)

```text
Field::ShadowOps::write(IntT)
  -> signed/unsigned compile-time dispatch
  -> RegisterState::shadow_write_signed(...) or shadow_write_unsigned(...)
      -> optional range check
      -> encode field bits into register write-shadow
      -> set dirty_ if staged value changed
```

Important: staged write updates register **write-shadow only**.

## 6. Block And Top Shadow APIs

Container classes (regfile/addrmap) expose:

- `shadow.read_hw()`
- `shadow.flush()`
- `shadow.flush_always()`

Internal helpers:

- `shadow_read_hw_impl()`
- `shadow_flush_impl()`
- `shadow_flush_always_impl()`

These recurse through children; registers are flushed only if
`supports_shadow_write()` is true.

## 7. Top Class (`DemoRoot`)

User instantiation:

```cpp
demo::DemoRoot<BusT> my_root(bus, base_address);
```

Top-level responsibilities:

- construct hierarchy and arrays (for this design: `regfile_1[2]`)
- expose status API (`ok()`, `last_error()`, `clear_error()`)
- expose addrmap shadow operations (`read_hw`, `flush`, `flush_always`)

## 8. Mapping To `design.rdl`

- `addrmap my_root` -> `DemoRoot`
- `regfile_1[2]` -> `detail::NodeArray<..., 2> regfile_1`
- `sub_regfile` -> `MyRootRegfile1SubRegfileBlk`
- `example_reg` -> `MyRootRegfile1SubRegfileExampleRegReg`
- fields:
  - `enable[0]` -> `enable`
  - `mode[3:1]` -> `mode`
  - `status[7:4]` (`sw=r`) -> read-only API

## 9. Regenerate Command

```bash
. .venv/bin/activate
peakrdl cpp examples/basic/design.rdl \
  -o examples/basic/demo_regs.hpp \
  --namespace demo \
  --class-name DemoRoot \
  --error-style exceptions
```
