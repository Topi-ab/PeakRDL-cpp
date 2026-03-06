# `demo_regs.hpp` Walkthrough

This document explains the current generated header:

- `examples/basic/demo_regs.hpp`

It is based on:

- `examples/basic/design.rdl`

If you regenerate the header, line numbers may shift.

## 1. File Preamble And Global Types

See `demo_regs.hpp:15-20`:

- `namespace demo`
- `using addr_t = std::uint32_t;`
- `using data_t = std::uint32_t;`
- `kAccessWidth = 32`
- `kCheckWriteRange = true`

These are global generation-time choices for this design.

## 2. `detail` Runtime Layer

See `demo_regs.hpp:22-295`.

This section is internal plumbing used by all generated nodes.

### 2.1 `detail::Context`

See `demo_regs.hpp:30-57`.

Holds:

- bus pointer
- base address
- error state (`ok`, `last_error`, `clear_error`)

`fail()` either throws or records status depending on `kThrowOnError`.

### 2.2 `detail::NodeArray`

See `demo_regs.hpp:66-75`.

Array wrapper for RDL arrays, exposing:

- `operator[]`
- `size()`

### 2.3 `detail::RegisterState`

See `demo_regs.hpp:77-293`.

This is the core register engine. It owns:

- address math (`address()`)
- hardware read (`read_hw()`)
- direct writes (`direct_write_unsigned/signed`)
- shadow writes (`shadow_write_unsigned/signed`)
- shadow flush/read_hw
- dirty tracking
- side-effect behavior (`rclr`, `rset`, `singlepulse`)

Important methods:

- `read_hw()` (`108-112`): bus read and shadow update.
- `flush()` (`118-129`): writes shadow to HW if dirty.
- `read_field_hw()` / `read_field_shadow()` (`131-139`): field extraction.
- `check_signed()` (`249-262`): signed range validation.

## 3. Register Class

See `MyRootRegfile1SubRegfileExampleRegReg` at `demo_regs.hpp:297-469`.

This corresponds to:

- `my_root.regfile_1[].sub_regfile.example_reg`

Main members:

- `read()`, `address()`, `supports_shadow_write()`
- nested `ShadowOps` with `read_hw()`, `flush()`, `dirty()`
- field objects: `enable`, `mode`, `status`

## 4. Field Classes

See, for example:

- `enable`: `326-379`
- `mode`: `380-433`
- `status`: `434-465`

Each field class includes:

- constants: `LSB`, `MSB`, `WIDTH`, `MASK`, `SW`, `ONREAD`
- `read()` only if `sw` allows reads
- `write(IntT)` only if `sw` allows writes
- nested `shadow` API (`shadow.read()`, optional `shadow.write(IntT)`)

### 4.1 Signed vs Unsigned Write Dispatch

In `write(IntT)` and `shadow.write(IntT)`:

- compile-time `static_assert` requires integral type and rejects `bool`
- `if constexpr (std::is_signed_v<IntT>)` chooses signed path
- otherwise unsigned path is used

See `enable.write` at `342-351` and `360-368`.

## 5. Field Access Call Graphs

The graphs below use `enable` as the concrete example, but the same structure
applies to other writable/readable fields.

### 5.1 `field.read()` (hardware access)

Example: `my_root.regfile_1[i].sub_regfile.example_reg.enable.read()`

```text
Field::read()
  -> RegisterState::read_field_hw(MASK, LSB, field_name)
      -> RegisterState::read_hw()
          -> bus->read(address)
          -> RegisterState::apply_hw_read(raw)
      -> (raw & MASK) >> LSB
```

Reference points:

- `enable.read()` at `338-340`
- `read_field_hw()` at `131-135`
- `read_hw()` at `108-112`
- `apply_hw_read()` at `241-247`

### 5.2 `field.write(value)` (direct hardware write path)

Example: `...enable.write(v)`

```text
Field::write(IntT)
  -> compile-time signed/unsigned dispatch (if constexpr)
  -> RegisterState::direct_write_signed(...) or direct_write_unsigned(...)
      -> optional range check (kCheckWriteRange)
      -> base_value = read_hw() for rw fields, or write_shadow_ for w fields
      -> encode field bits into full register value
      -> bus->write(address, next)
      -> update write_shadow_ and read_shadow_
      -> clear singlepulse bits if needed
```

Reference points:

- `enable.write(IntT)` at `342-351`
- `direct_write_unsigned()` at `187-220`
- `direct_write_signed()` at `222-238`

### 5.3 `field.shadow.read()` (shadow-only read)

Example: `...enable.shadow.read()`

```text
Field::ShadowOps::read()
  -> RegisterState::read_field_shadow(MASK, LSB)
      -> (write_shadow_ & MASK) >> LSB
```

Reference points:

- `enable.shadow.read()` at `356-358`
- `read_field_shadow()` at `137-139`

### 5.4 `field.shadow.write(value)` (shadow-only write)

Example: `...enable.shadow.write(v)`

```text
Field::ShadowOps::write(IntT)
  -> compile-time signed/unsigned dispatch (if constexpr)
  -> RegisterState::shadow_write_signed(...) or shadow_write_unsigned(...)
      -> optional range check (kCheckWriteRange)
      -> encode field bits into write_shadow_
      -> mark dirty_ when value changed
      -> mirror bits into read_shadow_
```

Reference points:

- `enable.shadow.write(IntT)` at `360-368`
- `shadow_write_unsigned()` at `141-165`
- `shadow_write_signed()` at `167-185`

## 6. Block (Container) Classes

Generated from regfiles:

- `MyRootRegfile1SubRegfileBlk` (`472-507`)
- `MyRootRegfile1Blk` (`509-543`)

Each block provides:

- hierarchical members (`example_reg`, `sub_regfile`)
- block-level `shadow.read_hw()` and `shadow.flush()`

## 7. Top Class

See `DemoRoot` at `545-597`.

This is the class you instantiate in user code:

```cpp
demo::DemoRoot<BusT> my_root(bus, base);
```

Top-level responsibilities:

- create array elements (`regfile_1[0]`, `regfile_1[1]`) in constructor
- expose error API (`ok`, `last_error`, `clear_error`)
- expose addrmap-level `shadow.read_hw()` and `shadow.flush()`

## 8. Mapping Back To `design.rdl`

From `design.rdl`:

- `addrmap my_root` -> top class `DemoRoot`
- `regfile_1[2]` -> `NodeArray<..., 2> regfile_1`
- `sub_regfile` -> `MyRootRegfile1SubRegfileBlk`
- `example_reg` -> `MyRootRegfile1SubRegfileExampleRegReg`
- fields:
  - `enable[0]` -> `enable` field class
  - `mode[3:1]` -> `mode` field class
  - `status[7:4]` (`sw=r`) -> read-only field API

## 9. Regeneration Command

From repo root:

```bash
. .venv/bin/activate
peakrdl cpp examples/basic/design.rdl \
  -o examples/basic/demo_regs.hpp \
  --namespace demo \
  --class-name DemoRoot \
  --error-style exceptions
```
