# Copyright (c) 2026 PeakRDL-cpp contributors
# SPDX-License-Identifier: LGPL-3.0-or-later

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from systemrdl import RDLCompiler

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


def test_single_bit_rw_field_accepts_unsigned_and_signed_values() -> None:
    case_dir = _reset_case_dir("single_bit_rw_signed_unsigned")

    rdl_text = """
    addrmap top {
      reg {
        field {
          sw = rw;
          hw = r;
        } flag[0:0] = 1'b0;
      } control @0x0;
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
        class_name="BitRoot",
        error_style="exceptions",
    )

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
    concept HasUnsignedWrite = requires(T f) { f.write(std::uint32_t{1}); };

    template <typename T>
    concept HasSignedWrite = requires(T f) { f.write(std::int32_t{-1}); };

    int main() {
        using Root = demo::BitRoot<MockBus>;
        using FlagField = decltype(std::declval<Root&>().control.flag);

        static_assert(std::same_as<decltype(std::declval<FlagField&>().read()), demo::data_t>);
        static_assert(HasUnsignedWrite<FlagField>);
        static_assert(HasSignedWrite<FlagField>);

        MockBus bus;
        Root root(bus, 0);

        root.control.flag.write(std::uint32_t{0});
        assert((bus.mem[0] & 0x1u) == 0u);
        assert(root.control.flag.read() == 0u);

        root.control.flag.write(std::uint32_t{1});
        assert((bus.mem[0] & 0x1u) == 1u);
        assert(root.control.flag.read() == 1u);

        root.control.flag.write(std::int32_t{0});
        assert((bus.mem[0] & 0x1u) == 0u);
        assert(root.control.flag.read() == 0u);

        root.control.flag.write(std::int32_t{-1});
        assert((bus.mem[0] & 0x1u) == 1u);
        assert(root.control.flag.read() == 1u);

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
