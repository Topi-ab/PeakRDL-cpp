# Copyright (c) 2026 PeakRDL-cpp contributors
# SPDX-License-Identifier: LGPL-3.0-or-later

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from systemrdl import RDLCompiler

from peakrdl_cpp import CppExporter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / "tmp" / "pytest_cases"
FIXTURE_RDL_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "rdl"


def _fixture_rdl_files() -> list[Path]:
    if not FIXTURE_RDL_ROOT.exists():
        return []
    return sorted(FIXTURE_RDL_ROOT.rglob("*.rdl"))


def _fixture_case_id(path: Path) -> str:
    rel = path.relative_to(FIXTURE_RDL_ROOT)
    return "__".join(rel.parts).replace(".rdl", "")


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


def _build_and_run_cpp(cpp_file: Path, exe_file: Path) -> None:
    subprocess.run(
        [
            "g++",
            "-std=c++20",
            str(cpp_file),
            "-o",
            str(exe_file),
        ],
        check=True,
        cwd=PROJECT_ROOT,
    )
    subprocess.run([str(exe_file)], check=True, cwd=PROJECT_ROOT)


def test_end_to_end_generate_compile_and_run() -> None:
    case_dir = _reset_case_dir("e2e")

    rdl_text = """
    addrmap my_root {
      regfile regfile_1_t {
        regfile sub_regfile_t {
          reg {
            field { sw=rw; } rwf[3:0] = 4'h0;
            field { sw=r; } rof[7:4] = 4'h0;
            field { sw=w; } wof[11:8] = 4'h0;
            field { sw=rw; singlepulse; } pulse[12:12] = 1'b0;
          } example_reg @0x0;

          reg {
            field { sw=rw; onread=rclr; } rc[0:0] = 1'b0;
            field { sw=rw; onread=rset; } rs[1:1] = 1'b0;
          } status_reg @0x4;
        } sub_regfile @0x20;
      } regfile_1[4] @0x100 += 0x40;

      reg {
        field { sw=rw; onwrite=woset; } bad[0:0] = 1'b0;
      } unsupported_reg @0x500;
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
        class_name="MyRoot",
        error_style="exceptions",
    )
    generated_hpp = hpp_file.read_text(encoding="utf-8")
    assert "std::int64_t" not in generated_hpp
    assert "signed_input_t" in generated_hpp

    cpp_test = f"""
    #include <cassert>
    #include <cstdint>
    #include <concepts>
    #include <unordered_map>
    #include <utility>

    #include \"{hpp_file}\"

    struct MockBus {{
        std::unordered_map<std::uint64_t, std::uint64_t> mem;
        std::unordered_map<std::uint64_t, std::uint64_t> read_count;
        std::unordered_map<std::uint64_t, std::uint64_t> write_count;

        std::uint64_t read(std::uint64_t addr) {{
            read_count[addr]++;
            return mem[addr];
        }}

        void write(std::uint64_t addr, std::uint64_t value) {{
            write_count[addr]++;
            mem[addr] = value;
        }}
    }};

    template <typename T>
    concept HasWrite = requires(T f) {{ f.write(std::uint64_t{{1}}); }};

    template <typename T>
    concept HasRead = requires(T f) {{ f.read(); }};

    int main() {{
        using Root = demo::MyRoot<MockBus>;

        using RoField = decltype(std::declval<Root&>().regfile_1[0].sub_regfile.example_reg.rof);
        using RwField = decltype(std::declval<Root&>().regfile_1[0].sub_regfile.example_reg.rwf);
        using WoField = decltype(std::declval<Root&>().regfile_1[0].sub_regfile.example_reg.wof);
        using OnwriteField = decltype(std::declval<Root&>().unsupported_reg.bad);

        static_assert(!HasWrite<RoField>);
        static_assert(HasWrite<RwField>);
        static_assert(!HasRead<WoField>);
        static_assert(!HasWrite<OnwriteField>);

        MockBus bus;
        Root my_root(bus, 0);

        constexpr std::uint64_t kExampleRegAddr = 0x1E0ull;
        constexpr std::uint64_t kStatusRegAddr = 0x1E4ull;
        constexpr std::uint64_t kUnsupportedRegAddr = 0x500ull;

        bus.mem[kExampleRegAddr] = 0xA0ull;

        my_root.regfile_1[3].sub_regfile.example_reg.rwf.write(std::uint64_t{{0x3}});
        assert(bus.read_count[kExampleRegAddr] == 1);
        assert(bus.mem[kExampleRegAddr] == 0xA3ull);

        std::uint64_t reads_before_wo_write = bus.read_count[kExampleRegAddr];
        my_root.regfile_1[3].sub_regfile.example_reg.wof.write(std::uint64_t{{0x5}});
        assert(bus.read_count[kExampleRegAddr] == reads_before_wo_write);
        assert(bus.mem[kExampleRegAddr] == 0x5A3ull);

        std::uint64_t wof_shadow_before = my_root.regfile_1[3].sub_regfile.example_reg.wof.rd_shadow.read();
        bus.mem[kExampleRegAddr] = 0x2F3ull;
        std::uint64_t rwf_read = my_root.regfile_1[3].sub_regfile.example_reg.rwf.read();
        assert(rwf_read == 0x3ull);
        assert(my_root.regfile_1[3].sub_regfile.example_reg.wof.rd_shadow.read() == wof_shadow_before);

        my_root.regfile_1[3].sub_regfile.example_reg.rwf.wr_shadow.write(std::uint64_t{{0x9}});
        my_root.regfile_1[3].sub_regfile.example_reg.wof.wr_shadow.write(std::uint64_t{{0x3}});
        assert(my_root.regfile_1[3].sub_regfile.example_reg.wr_shadow.dirty());

        std::uint64_t writes_before_flush = bus.write_count[kExampleRegAddr];
        my_root.regfile_1[3].wr_shadow.flush();
        assert(bus.write_count[kExampleRegAddr] == writes_before_flush + 1);
        assert(bus.mem[kExampleRegAddr] == 0x3F9ull);

        std::uint64_t writes_before_flush_always = bus.write_count[kExampleRegAddr];
        my_root.regfile_1[3].sub_regfile.example_reg.wr_shadow.flush_always();
        assert(bus.write_count[kExampleRegAddr] == writes_before_flush_always + 1);
        assert(!my_root.regfile_1[3].sub_regfile.example_reg.wr_shadow.dirty());

        my_root.regfile_1[3].sub_regfile.example_reg.pulse.wr_shadow.write(std::uint64_t{{1}});
        my_root.regfile_1[3].sub_regfile.example_reg.wr_shadow.flush();
        assert(my_root.regfile_1[3].sub_regfile.example_reg.pulse.rd_shadow.read() == 0u);

        bus.mem[kStatusRegAddr] = 0x3ull;
        std::uint64_t rc = my_root.regfile_1[3].sub_regfile.status_reg.rc.read();
        assert(rc == 1u);
        assert(my_root.regfile_1[3].sub_regfile.status_reg.rc.rd_shadow.read() == 1u);
        assert(my_root.regfile_1[3].sub_regfile.status_reg.rs.rd_shadow.read() == 1u);

        std::uint64_t unsupported_reads_before = bus.read_count[kUnsupportedRegAddr];
        my_root.rd_shadow.read_hw();
        assert(bus.read_count[kUnsupportedRegAddr] == unsupported_reads_before);

        std::uint64_t unsupported_writes_before = bus.write_count[kUnsupportedRegAddr];
        my_root.wr_shadow.flush();
        assert(bus.write_count[kUnsupportedRegAddr] == unsupported_writes_before);
        my_root.wr_shadow.flush_always();
        assert(bus.write_count[kUnsupportedRegAddr] == unsupported_writes_before);

        assert(my_root.ok());
        return 0;
    }}
    """

    cpp_file.write_text(cpp_test, encoding="utf-8")
    _build_and_run_cpp(cpp_file, exe_file)


def test_direct_write_preserves_write_only_bits_when_hw_read_masks_them() -> None:
    case_dir = _reset_case_dir("wo_merge_preserve")

    rdl_text = """
    addrmap top {
      reg {
        field { sw=rw; } rwf[3:0] = 4'h0;
        field { sw=w; } wof[11:8] = 4'h0;
      } mix @0x0;
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
        class_name="TopRoot",
        error_style="exceptions",
    )

    cpp_test = f"""
    #include <cassert>
    #include <cstdint>
    #include <unordered_map>

    #include \"{hpp_file}\"

    struct MockBus {{
        static constexpr std::uint64_t kWofMask = 0xF00ull;
        std::unordered_map<std::uint64_t, std::uint64_t> mem;
        std::unordered_map<std::uint64_t, std::uint64_t> read_count;
        std::unordered_map<std::uint64_t, std::uint64_t> write_count;

        std::uint64_t read(std::uint64_t addr) {{
            read_count[addr]++;
            // Emulate write-only bits as unreadable from HW.
            return mem[addr] & ~kWofMask;
        }}

        void write(std::uint64_t addr, std::uint64_t value) {{
            write_count[addr]++;
            mem[addr] = value;
        }}
    }};

    int main() {{
        using Root = demo::TopRoot<MockBus>;
        MockBus bus;
        Root my_root(bus, 0);

        constexpr std::uint64_t kRegAddr = 0x0ull;
        bus.mem[kRegAddr] = 0x0ull;

        my_root.mix.wof.write(std::uint64_t{{0xA}});
        assert(bus.mem[kRegAddr] == 0xA00ull);

        std::uint64_t reads_before = bus.read_count[kRegAddr];
        my_root.mix.rwf.write(std::uint64_t{{0x3}});
        assert(bus.read_count[kRegAddr] == reads_before + 1);

        // WO bits must be preserved from shadow, not lost due to masked HW read().
        assert(bus.mem[kRegAddr] == 0xA03ull);
        assert(my_root.ok());
        return 0;
    }}
    """

    cpp_file.write_text(cpp_test, encoding="utf-8")
    _build_and_run_cpp(cpp_file, exe_file)


def test_status_error_mode_no_throw() -> None:
    case_dir = _reset_case_dir("status_mode")

    rdl_text = """
    addrmap top {
      reg {
        field { sw=rw; } f[3:0] = 4'h0;
      } r0 @0x0;
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
        class_name="TopRoot",
        error_style="status",
    )

    cpp_test = f"""
    #include <cassert>
    #include <cstdint>
    #include <unordered_map>

    #include \"{hpp_file}\"

    struct MockBus {{
        std::unordered_map<std::uint64_t, std::uint64_t> mem;
        std::uint64_t read(std::uint64_t addr) {{ return mem[addr]; }}
        void write(std::uint64_t addr, std::uint64_t value) {{ mem[addr] = value; }}
    }};

    int main() {{
        MockBus bus;
        demo::TopRoot<MockBus> root(bus, 0);

        root.r0.f.write(std::uint64_t{{32}});
        assert(!root.ok());
        root.clear_error();
        assert(root.ok());

        return 0;
    }}
    """

    cpp_file.write_text(cpp_test, encoding="utf-8")
    _build_and_run_cpp(cpp_file, exe_file)


def test_shadow_read_uses_read_shadow_not_write_shadow() -> None:
    case_dir = _reset_case_dir("shadow_read_from_read_shadow")

    rdl_text = """
    addrmap top {
      reg {
        field { sw=rw; } f[3:0] = 4'h0;
      } r0 @0x0;
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
        class_name="TopRoot",
        error_style="exceptions",
    )

    cpp_test = f"""
    #include <cassert>
    #include <cstdint>
    #include <unordered_map>

    #include \"{hpp_file}\"

    struct MockBus {{
        std::unordered_map<std::uint64_t, std::uint64_t> mem;
        std::uint64_t read(std::uint64_t addr) {{ return mem[addr]; }}
        void write(std::uint64_t addr, std::uint64_t value) {{ mem[addr] = value; }}
    }};

    int main() {{
        MockBus bus;
        demo::TopRoot<MockBus> root(bus, 0);

        constexpr std::uint64_t kAddr = 0x0ull;
        bus.mem[kAddr] = 0x0ull;

        root.r0.f.wr_shadow.write(std::uint64_t{{5}});
        assert(root.r0.wr_shadow.dirty());
        assert(root.r0.f.rd_shadow.read() == 0u);
        assert(root.r0.f.wr_shadow.read() == 5u);

        bus.mem[kAddr] = 0x9ull;
        assert(root.r0.f.read() == 0x9ull);
        assert(root.r0.f.rd_shadow.read() == 0x9ull);
        assert(root.r0.f.wr_shadow.read() == 0x9ull);

        root.r0.f.wr_shadow.write(std::uint64_t{{3}});
        assert(root.r0.f.rd_shadow.read() == 0x9ull);
        assert(root.r0.f.wr_shadow.read() == 0x3ull);

        root.r0.wr_shadow.flush();
        assert(bus.mem[kAddr] == 0x3ull);
        assert(root.r0.f.rd_shadow.read() == 0x9ull);
        assert(root.r0.f.wr_shadow.read() == 0x3ull);

        root.r0.rd_shadow.read_hw();
        assert(root.r0.f.rd_shadow.read() == 0x3ull);
        assert(root.ok());
        return 0;
    }}
    """

    cpp_file.write_text(cpp_test, encoding="utf-8")
    _build_and_run_cpp(cpp_file, exe_file)


def test_status_error_mode_no_write_range_check() -> None:
    case_dir = _reset_case_dir("status_mode_no_range_check")

    rdl_text = """
    addrmap top {
      reg {
        field { sw=rw; } f[3:0] = 4'h0;
      } r0 @0x0;
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
        class_name="TopRootNoRangeCheck",
        error_style="status",
        check_write_range=False,
    )
    generated_hpp = hpp_file.read_text(encoding="utf-8")
    assert "static constexpr bool kCheckWriteRange = false;" in generated_hpp

    cpp_test = f"""
    #include <cassert>
    #include <cstdint>
    #include <unordered_map>

    #include \"{hpp_file}\"

    struct MockBus {{
        std::unordered_map<std::uint64_t, std::uint64_t> mem;
        std::uint64_t read(std::uint64_t addr) {{ return mem[addr]; }}
        void write(std::uint64_t addr, std::uint64_t value) {{ mem[addr] = value; }}
    }};

    int main() {{
        MockBus bus;
        demo::TopRootNoRangeCheck<MockBus> root(bus, 0);

        root.r0.f.write(std::uint64_t{{32}});
        assert(root.ok());

        return 0;
    }}
    """

    cpp_file.write_text(cpp_test, encoding="utf-8")
    _build_and_run_cpp(cpp_file, exe_file)


def test_status_error_mode_detects_unsigned_precast_overflow() -> None:
    case_dir = _reset_case_dir("status_mode_unsigned_precast_overflow")

    rdl_text = """
    addrmap top {
      default regwidth = 8;
      default accesswidth = 8;
      reg {
        field { sw=rw; } f[7:0] = 8'h0;
      } r0 @0x0;
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
        class_name="TopRootRangeCheck8",
        error_style="status",
    )

    cpp_test = f"""
    #include <cassert>
    #include <cstdint>
    #include <unordered_map>

    #include \"{hpp_file}\"

    struct MockBus {{
        std::unordered_map<std::uint64_t, std::uint64_t> mem;
        std::uint64_t read(std::uint64_t addr) {{ return mem[addr]; }}
        void write(std::uint64_t addr, std::uint64_t value) {{ mem[addr] = value; }}
    }};

    int main() {{
        MockBus bus;
        demo::TopRootRangeCheck8<MockBus> root(bus, 0);

        root.r0.f.write(std::uint64_t{{256}});
        assert(!root.ok());

        return 0;
    }}
    """

    cpp_file.write_text(cpp_test, encoding="utf-8")
    _build_and_run_cpp(cpp_file, exe_file)


def test_basic_example_generate_in_place_and_run() -> None:
    rdl_file = PROJECT_ROOT / "examples" / "basic" / "design.rdl"
    hpp_file = PROJECT_ROOT / "examples" / "basic" / "demo_regs.hpp"
    main_cpp = PROJECT_ROOT / "examples" / "basic" / "main.cpp"
    exe_file = PROJECT_ROOT / "examples" / "basic" / "demo_example"

    top = _compile_top(rdl_file)
    CppExporter().export(
        top,
        hpp_file,
        namespace="demo",
        class_name="DemoRoot",
        error_style="exceptions",
    )

    subprocess.run(
        [
            "g++",
            "-std=c++20",
            "-Iexamples/basic",
            str(main_cpp),
            "-o",
            str(exe_file),
        ],
        check=True,
        cwd=PROJECT_ROOT,
    )
    subprocess.run([str(exe_file)], check=True, cwd=PROJECT_ROOT)


def test_generation_fails_on_address_offset_overflow() -> None:
    case_dir = _reset_case_dir("address_overflow")

    rdl_text = """
    addrmap top {
      reg {
        field { sw=rw; } f[0:0] = 1'b0;
      } r0 @0x100000000;
    };
    """

    rdl_file = case_dir / "design.rdl"
    out_file = case_dir / "regs.hpp"
    rdl_file.write_text(rdl_text, encoding="utf-8")

    top = _compile_top(rdl_file)

    with pytest.raises(ValueError, match="does not fit addr_t"):
        CppExporter().export(top, out_file, namespace="demo", class_name="Top")


def test_generation_fails_on_access_span_overflow_for_64bit_access() -> None:
    case_dir = _reset_case_dir("address_span_overflow_64")

    rdl_text = """
    addrmap top {
      reg {
        accesswidth = 64;
        regwidth = 64;
        field { sw=rw; } f[63:0] = 64'h0;
      } r0 @0xFFFFFFFE;
    };
    """

    rdl_file = case_dir / "design.rdl"
    out_file = case_dir / "regs.hpp"
    rdl_file.write_text(rdl_text, encoding="utf-8")

    top = _compile_top(rdl_file)

    with pytest.raises(ValueError, match="Address span overflow"):
        CppExporter().export(top, out_file, namespace="demo", class_name="Top")


def test_generation_fails_on_wr_shadow_impl_name_conflict() -> None:
    case_dir = _reset_case_dir("wr_shadow_impl_name_conflict")

    rdl_text = """
    addrmap top {
      reg {
        field { sw=rw; } f[0:0] = 1'b0;
      } wr_shadow_flush_impl @0x0;
    };
    """

    rdl_file = case_dir / "design.rdl"
    out_file = case_dir / "regs.hpp"
    rdl_file.write_text(rdl_text, encoding="utf-8")

    top = _compile_top(rdl_file)

    with pytest.raises(ValueError, match="reserved generated API symbol"):
        CppExporter().export(top, out_file, namespace="demo", class_name="Top")


def test_generation_fails_on_wr_shadow_flush_always_impl_name_conflict() -> None:
    case_dir = _reset_case_dir("wr_shadow_flush_always_impl_name_conflict")

    rdl_text = """
    addrmap top {
      reg {
        field { sw=rw; } f[0:0] = 1'b0;
      } wr_shadow_flush_always_impl @0x0;
    };
    """

    rdl_file = case_dir / "design.rdl"
    out_file = case_dir / "regs.hpp"
    rdl_file.write_text(rdl_text, encoding="utf-8")

    top = _compile_top(rdl_file)

    with pytest.raises(ValueError, match="reserved generated API symbol"):
        CppExporter().export(top, out_file, namespace="demo", class_name="Top")


def test_generation_fails_on_reserved_name_conflict() -> None:
    case_dir = _reset_case_dir("name_conflict")

    rdl_text = """
    addrmap top {
      reg {
        field { sw=rw; } rd_shadow[0:0] = 1'b0;
      } r0 @0x0;
    };
    """

    rdl_file = case_dir / "design.rdl"
    out_file = case_dir / "regs.hpp"
    rdl_file.write_text(rdl_text, encoding="utf-8")

    top = _compile_top(rdl_file)

    with pytest.raises(ValueError, match="reserved generated API symbol"):
        CppExporter().export(top, out_file, namespace="demo", class_name="Top")


def test_generation_fails_on_mixed_accesswidth() -> None:
    case_dir = _reset_case_dir("mixed_accesswidth")

    rdl_text = """
    addrmap top {
      reg {
        field { sw=rw; } a[31:0] = 32'h0;
      } r0 @0x0;

      reg {
        accesswidth = 16;
        field { sw=rw; } b[15:0] = 16'h0;
      } r1 @0x4;
    };
    """

    rdl_file = case_dir / "design.rdl"
    out_file = case_dir / "regs.hpp"
    rdl_file.write_text(rdl_text, encoding="utf-8")

    top = _compile_top(rdl_file)

    with pytest.raises(ValueError, match="Multiple accesswidth values"):
        CppExporter().export(top, out_file, namespace="demo", class_name="Top")


@pytest.mark.parametrize(
    "fixture_rdl",
    _fixture_rdl_files(),
    ids=lambda p: _fixture_case_id(p),
)
def test_fixture_rdls_generate_compile_and_run_smoke(fixture_rdl: Path) -> None:
    case_id = _fixture_case_id(fixture_rdl)
    case_dir = _reset_case_dir(f"fixture_smoke_{case_id}")

    rdl_file = case_dir / "design.rdl"
    hpp_file = case_dir / "regs.hpp"
    cpp_file = case_dir / "test.cpp"
    exe_file = case_dir / "test_bin"

    shutil.copyfile(fixture_rdl, rdl_file)

    top = _compile_top(rdl_file)
    CppExporter().export(
        top,
        hpp_file,
        namespace="demo",
        class_name="FixtureRoot",
        error_style="exceptions",
    )

    cpp_test = """
    #include <cstdint>
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

    int main() {
        MockBus bus;
        demo::FixtureRoot<MockBus> root(bus, static_cast<demo::addr_t>(0));
        return root.ok() ? 0 : 1;
    }
    """

    cpp_file.write_text(cpp_test, encoding="utf-8")
    subprocess.run(
        [
            "g++",
            "-std=c++20",
            str(cpp_file.name),
            "-o",
            str(exe_file.name),
        ],
        check=True,
        cwd=case_dir,
    )
    subprocess.run([str(exe_file)], check=True, cwd=case_dir)
