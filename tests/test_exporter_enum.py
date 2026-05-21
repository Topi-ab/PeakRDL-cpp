# Copyright (c) 2026 PeakRDL-cpp contributors
# SPDX-License-Identifier: LGPL-3.0-or-later

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from systemrdl import RDLCompiler
from systemrdl.messages import RDLCompileError

from peakrdl_cpp import CppExporter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / "tmp" / "pytest_cases"


def _reset_case_dir(name: str) -> Path:
    case_dir = TEST_TMP_ROOT / name
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir


def _compile_top(rdl_path: Path):
    compiler = RDLCompiler()
    compiler.compile_file(str(rdl_path))
    root = compiler.elaborate()
    return root.top


def test_enum_fields_generate_typed_cpp_api() -> None:
    case_dir = _reset_case_dir("enum_typed_api")

    rdl_text = """
    enum mode_e {
      idle = 0;
      run = 1;
      sleep = 2;
      fault = 3;
    };

    enum state_e {
      boot = 0;
      ready = 1;
      error = 2;
    };

    addrmap top {
      reg {
        field {
          sw = rw;
          hw = r;
          encode = mode_e;
        } mode[1:0] = 0;

        field {
          sw = r;
          hw = w;
          encode = state_e;
        } state[5:4] = 1;
      } status @0x0;
    };
    """

    rdl_file = case_dir / "design.rdl"
    hpp_file = case_dir / "regs.hpp"
    cpp_file = case_dir / "test.cpp"
    exe_file = case_dir / "test_bin"
    rdl_file.write_text(rdl_text, encoding="utf-8")

    top = _compile_top(rdl_file)
    CppExporter().export(
        top,
        hpp_file,
        namespace="demo",
        class_name="EnumRoot",
        error_style="exceptions",
    )

    generated_hpp = hpp_file.read_text(encoding="utf-8")
    assert "enum class mode_e : data_t" in generated_hpp
    assert "enum class state_e : data_t" in generated_hpp
    assert "mode_e read()" in generated_hpp
    assert "void write(mode_e value)" in generated_hpp

    cpp_test = """
    #include <cassert>
    #include <concepts>
    #include <cstdint>
    #include <type_traits>
    #include <unordered_map>

    #include "regs.hpp"

    struct MockBus {
        std::unordered_map<demo::addr_t, demo::data_t> mem;

        demo::data_t read(demo::addr_t addr) {
            return mem[addr];
        }

        void write(demo::addr_t addr, demo::data_t value) {
            mem[addr] = value;
        }
    };

    template <typename T>
    concept HasIntegerWrite = requires(T f) { f.write(1u); };

    template <typename T>
    concept HasModeWrite = requires(T f) { f.write(demo::mode_e::run); };

    int main() {
        using Root = demo::EnumRoot<MockBus>;
        using ModeField = decltype(std::declval<Root&>().status.mode);

        static_assert(std::is_enum_v<demo::mode_e>);
        static_assert(std::is_enum_v<demo::state_e>);
        static_assert(std::same_as<decltype(std::declval<ModeField&>().read()), demo::mode_e>);
        static_assert(!HasIntegerWrite<ModeField>);
        static_assert(HasModeWrite<ModeField>);

        MockBus bus;
        Root root(bus, 0);

        bus.mem[0] = 0x20u;
        assert(root.status.state.read() == demo::state_e::error);

        root.status.mode.write(demo::mode_e::sleep);
        assert((bus.mem[0] & 0x3u) == 0x2u);
        assert(root.status.mode.rd_shadow.read() == demo::mode_e::sleep);

        root.status.mode.wr_shadow.write(demo::mode_e::fault);
        assert(root.status.mode.wr_shadow.read() == demo::mode_e::fault);
        root.status.wr_shadow.flush();
        assert((bus.mem[0] & 0x3u) == 0x3u);

        assert(root.ok());
        return 0;
    }
    """

    cpp_file.write_text(cpp_test, encoding="utf-8")
    subprocess.run(
        ["g++", "-std=c++20", str(cpp_file.name), "-o", str(exe_file.name)],
        check=True,
        cwd=case_dir,
    )
    subprocess.run([str(exe_file)], check=True, cwd=case_dir)


def test_enum_field_too_narrow_fails_in_systemrdl_elaboration() -> None:
    case_dir = _reset_case_dir("enum_too_narrow")

    rdl_text = """
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
          hw = r;
          encode = mode_e;
        } mode[0:0] = 0;
      } status @0x0;
    };
    """

    rdl_file = case_dir / "design.rdl"
    rdl_file.write_text(rdl_text, encoding="utf-8")

    with pytest.raises(RDLCompileError, match="Elaborate aborted"):
        _compile_top(rdl_file)
