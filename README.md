# peakrdl-cpp

`peakrdl-cpp` is a PeakRDL exporter plugin that generates C++ register access APIs
from SystemRDL designs.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

## Usage

Generate a C++ header from an RDL file:

```bash
peakrdl cpp design.rdl -o regs.hpp \
  --namespace my_block \
  --class-name MyBlock \
  --error-style exceptions
```

Use the Python API directly:

```python
from systemrdl import RDLCompiler
from peakrdl_cpp import CppExporter

compiler = RDLCompiler()
compiler.compile_file("design.rdl")
root = compiler.elaborate()

CppExporter().export(
    root.top,  # RootNode also works
    "regs.hpp",
    namespace="my_block",
    class_name="MyBlock",
    error_style="exceptions",   # or "status"
    check_write_range=True,     # optional, defaults to True
)
```

Generated API shape:

- Hierarchical object access with array indexing:
  - `my_root.regfile_1[3].sub_regfile.example_reg.field.write(13);`
  - `auto v = my_root.regfile_1[3].sub_regfile.example_reg.field.read();`
- Field APIs respect `sw` access rules:
  - `sw=r`: no `write()`
  - `sw=w`: no `read()`
- Shadow API:
  - `field.shadow.read()`, `field.shadow.write(...)`
  - `reg.shadow.read_hw()`, `reg.shadow.flush()`
  - `regfile.shadow.read_hw()`, `regfile.shadow.flush()`
  - `addrmap.shadow.read_hw()`, `addrmap.shadow.flush()`
- Hard generation error on reserved symbol conflicts (for example `shadow`).
- Error handling style:
  - `--error-style exceptions` throws `std::runtime_error`
  - `--error-style status` records errors and exposes `ok()/last_error()/clear_error()`
- Write range validation:
  - enabled by default
  - disable with `--no-write-range-check` to skip generated runtime range checks on `write()` and `shadow.write()`
- Access width:
  - `data_t` is deduced from SystemRDL `accesswidth`.
  - If more than one numeric `accesswidth` is present in the design, generation fails.
  - `addr_t` is fixed to `std::uint32_t`.

Bus adapter requirements:

- `data_t read(addr_t addr);`
- `void write(addr_t addr, data_t value);`

## Example

See checked-in example case:

- [examples/basic/README.md](examples/basic/README.md)
- [examples/basic/design.rdl](examples/basic/design.rdl)
- [examples/basic/main.cpp](examples/basic/main.cpp)

## Development

```bash
. .venv/bin/activate
pip install -e ".[test]"
pytest
```

Run adaptive compile/run checks against an external RDL file:

```bash
PEAKRDL_CPP_TEST_RDL=/abs/path/to/design.rdl \
  ./.venv/bin/pytest -q tests/test_exporter_adaptive.py
```

Optional deterministic seed override:

```bash
PEAKRDL_CPP_TEST_RDL=/abs/path/to/design.rdl \
PEAKRDL_CPP_TEST_SEED=42 \
  ./.venv/bin/pytest -q tests/test_exporter_adaptive.py
```

The test suite includes end-to-end validation:

- compile RDL
- generate C++
- compile and link C++ test bench against generated headers
- execute runtime behavior checks

## License

LGPL-3.0 (GNU Lesser General Public License v3.0). See [LICENSE](LICENSE).
