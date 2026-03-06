# Basic Example

This folder contains:

- `design.rdl`: SystemRDL input
- `main.cpp`: handwritten C++ usage example
- `HPP_WALKTHROUGH.md`: guided walkthrough of the generated header

`demo_regs.hpp` is intentionally not committed. Generate it from `design.rdl`.

## Generate Header

From repository root:

```bash
. .venv/bin/activate
peakrdl cpp examples/basic/design.rdl \
  -o examples/basic/demo_regs.hpp \
  --namespace demo \
  --class-name DemoRoot \
  --error-style exceptions
```

## Build And Run

```bash
g++ -std=c++20 -Iexamples/basic examples/basic/main.cpp -o examples/basic/demo_example
./examples/basic/demo_example
```
