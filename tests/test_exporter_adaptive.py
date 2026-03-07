# Copyright (c) 2026 PeakRDL-cpp contributors
# SPDX-License-Identifier: LGPL-3.0-or-later

from __future__ import annotations

import os
import random
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from systemrdl import RDLCompiler
from systemrdl.node import FieldNode, RegNode
from systemrdl.rdltypes import AccessType

from peakrdl_cpp import CppExporter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / "tmp" / "pytest_cases"
DEFAULT_RDL_PATH = PROJECT_ROOT / "examples" / "basic" / "design.rdl"
FIXTURE_RDL_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "rdl"

RDL_PATH_ENV = "PEAKRDL_CPP_TEST_RDL"
SEED_ENV = "PEAKRDL_CPP_TEST_SEED"


def _fixture_rdl_files() -> list[Path]:
    if not FIXTURE_RDL_ROOT.exists():
        return []
    return sorted(FIXTURE_RDL_ROOT.rglob("*.rdl"))


def _fixture_case_id(path: Path) -> str:
    rel = path.relative_to(FIXTURE_RDL_ROOT)
    return "__".join(rel.parts).replace(".rdl", "")


@dataclass(frozen=True)
class FieldTarget:
    reg_path_cpp: str
    field_path_cpp: str
    reg_addr: int
    field_low: int
    field_width: int
    access_width: int


def _reset_case_dir(name: str) -> Path:
    case_dir = TEST_TMP_ROOT / name
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir


def _build_and_run_cpp(case_dir: Path, cpp_file: Path, exe_file: Path) -> None:
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


def _compile_top(rdl_path: Path):
    compiler = RDLCompiler()
    compiler.compile_file(str(rdl_path))
    root = compiler.elaborate()
    return root.top


def _resolve_rdl_path() -> Path:
    configured = os.environ.get(RDL_PATH_ENV)
    if not configured:
        return DEFAULT_RDL_PATH

    configured_path = Path(configured)
    if not configured_path.is_absolute():
        configured_path = PROJECT_ROOT / configured_path
    return configured_path.resolve()


def _to_cpp_path(top_inst_name: str, full_path: str) -> str:
    if not full_path.startswith(top_inst_name):
        raise ValueError(f"Unexpected path '{full_path}' for top '{top_inst_name}'")
    rel = full_path[len(top_inst_name) :]
    if rel.startswith("."):
        rel = rel[1:]
    return f"my_root.{rel}" if rel else "my_root"


def _pick_simple_rw_field(top) -> FieldTarget:
    for reg in top.descendants(unroll=True):
        if not isinstance(reg, RegNode):
            continue

        access_width = int(reg.get_property("accesswidth"))
        if access_width <= 0 or access_width > 64:
            continue

        for field in reg.fields():
            if not isinstance(field, FieldNode):
                continue

            if field.get_property("sw") != AccessType.rw:
                continue
            if field.get_property("onread") is not None:
                continue
            if field.get_property("onwrite") is not None:
                continue
            if bool(field.get_property("singlepulse")):
                continue
            if bool(field.get_property("swwe")) or bool(field.get_property("swwel")):
                continue
            if bool(field.get_property("intr")):
                continue

            reg_path_cpp = _to_cpp_path(top.inst_name, reg.get_path())
            field_path_cpp = _to_cpp_path(top.inst_name, field.get_path())
            return FieldTarget(
                reg_path_cpp=reg_path_cpp,
                field_path_cpp=field_path_cpp,
                reg_addr=int(reg.absolute_address),
                field_low=int(field.low),
                field_width=int(field.high - field.low + 1),
                access_width=access_width,
            )

    raise ValueError(
        "Could not find a simple sw=rw field for adaptive runtime test. "
        "Need a field with no onread/onwrite/singlepulse/swwe/swwel/intr modifiers."
    )


def _field_mask(width: int, low: int) -> int:
    return ((1 << width) - 1) << low


def _data_mask(access_width: int) -> int:
    return (1 << access_width) - 1


def _choose_distinct_value(
    rng: random.Random,
    max_value: int,
    excluded: set[int],
    fallback: int,
) -> int:
    if max_value == 0:
        return 0

    for _ in range(128):
        candidate = rng.randint(0, max_value)
        if candidate not in excluded:
            return candidate

    if fallback not in excluded and fallback <= max_value:
        return fallback
    return 0


def _build_case_constants(
    target: FieldTarget,
    randomize: bool,
    seed: int,
) -> dict[str, int]:
    field_max = (1 << target.field_width) - 1
    data_mask = _data_mask(target.access_width)
    mask = _field_mask(target.field_width, target.field_low)

    rng = random.Random(seed)

    if randomize:
        init_raw = rng.randint(0, data_mask)
        hw_read_raw = rng.randint(0, data_mask)
        write_direct = _choose_distinct_value(
            rng,
            field_max,
            excluded={0},
            fallback=min(field_max, 1),
        )
    else:
        init_raw = data_mask & 0xA5A5A5A5A5A5A5A5
        hw_read_raw = data_mask & 0x5A5A5A5A5A5A5A5A
        write_direct = min(field_max, 1)

    expected_after_direct = (init_raw & ~mask) | ((write_direct << target.field_low) & mask)

    return {
        "init_raw": init_raw,
        "hw_read_raw": hw_read_raw,
        "write_direct": write_direct,
        "expected_after_direct": expected_after_direct & data_mask,
        "field_mask": mask & data_mask,
    }


def _render_cpp_test(target: FieldTarget, constants: dict[str, int], seed: int, randomize: bool) -> str:
    return f"""
    #include <cassert>
    #include <cstdint>
    #include <unordered_map>

    #include "regs.hpp"

    struct MockBus {{
        std::unordered_map<demo::addr_t, demo::data_t> mem;
        std::unordered_map<demo::addr_t, std::uint64_t> read_count;
        std::unordered_map<demo::addr_t, std::uint64_t> write_count;

        demo::data_t read(demo::addr_t addr) {{
            read_count[addr]++;
            return mem[addr];
        }}

        void write(demo::addr_t addr, demo::data_t value) {{
            write_count[addr]++;
            mem[addr] = value;
        }}
    }};

    int main() {{
        using Root = demo::AdaptiveRoot<MockBus>;
        MockBus bus;
        Root my_root(bus, static_cast<demo::addr_t>(0));

        constexpr bool kRandomized = {"true" if randomize else "false"};
        constexpr std::uint64_t kSeed = {seed}ull;

        constexpr demo::addr_t kRegAddr = static_cast<demo::addr_t>({target.reg_addr}u);
        constexpr demo::data_t kFieldMask = static_cast<demo::data_t>(0x{constants["field_mask"]:X}u);
        constexpr unsigned kFieldShift = {target.field_low}u;
        constexpr demo::data_t kFieldValueMask = static_cast<demo::data_t>(kFieldMask >> kFieldShift);

        constexpr demo::data_t kInitRaw = static_cast<demo::data_t>(0x{constants["init_raw"]:X}u);
        constexpr demo::data_t kHwReadRaw = static_cast<demo::data_t>(0x{constants["hw_read_raw"]:X}u);
        constexpr demo::data_t kDirectWriteValue = static_cast<demo::data_t>(0x{constants["write_direct"]:X}u);
        constexpr demo::data_t kExpectedAfterDirect = static_cast<demo::data_t>(0x{constants["expected_after_direct"]:X}u);

        // Keep seed and mode visible for easier reproduction in local debugging.
        (void)kRandomized;
        (void)kSeed;

        bus.mem[kRegAddr] = kInitRaw;

        std::uint64_t reads_before = bus.read_count[kRegAddr];
        {target.field_path_cpp}.write(kDirectWriteValue);
        assert(bus.read_count[kRegAddr] == reads_before + 1);
        assert(bus.mem[kRegAddr] == kExpectedAfterDirect);

        bus.mem[kRegAddr] = kHwReadRaw;
        demo::data_t read_value = {target.field_path_cpp}.read();
        demo::data_t expected_read = static_cast<demo::data_t>((kHwReadRaw & kFieldMask) >> kFieldShift);
        assert(read_value == expected_read);
        assert({target.field_path_cpp}.rd_shadow.read() == expected_read);
        assert({target.field_path_cpp}.wr_shadow.read() == expected_read);

        demo::data_t shadow_before = {target.field_path_cpp}.wr_shadow.read();
        demo::data_t shadow_write_value = static_cast<demo::data_t>(
            (shadow_before ^ static_cast<demo::data_t>(1u)) & kFieldValueMask
        );
        if (shadow_write_value == shadow_before) {{
            shadow_write_value = static_cast<demo::data_t>(
                (shadow_before + static_cast<demo::data_t>(1u)) & kFieldValueMask
            );
        }}
        assert(shadow_write_value != shadow_before);

        {target.field_path_cpp}.wr_shadow.write(shadow_write_value);
        assert({target.reg_path_cpp}.wr_shadow.dirty());
        assert({target.field_path_cpp}.wr_shadow.read() == shadow_write_value);
        assert({target.field_path_cpp}.rd_shadow.read() == expected_read);

        std::uint64_t writes_before = bus.write_count[kRegAddr];
        {target.reg_path_cpp}.wr_shadow.flush();
        assert(bus.write_count[kRegAddr] == writes_before + 1);
        assert((bus.mem[kRegAddr] & kFieldMask) == ((shadow_write_value << kFieldShift) & kFieldMask));

        assert(my_root.ok());
        return 0;
    }}
    """


def _run_adaptive_case(case_name: str, randomize: bool, source_rdl: Path | None = None) -> None:
    case_dir = _reset_case_dir(case_name)
    resolved_rdl = source_rdl if source_rdl is not None else _resolve_rdl_path()
    if not resolved_rdl.exists():
        pytest.skip(
            f"Configured RDL file does not exist: {resolved_rdl}. "
            f"Set {RDL_PATH_ENV} to a valid design path."
        )

    rdl_file = case_dir / "design.rdl"
    hpp_file = case_dir / "regs.hpp"
    cpp_file = case_dir / "test.cpp"
    exe_file = case_dir / "test_bin"

    shutil.copyfile(resolved_rdl, rdl_file)

    top = _compile_top(rdl_file)

    try:
        target = _pick_simple_rw_field(top)
    except ValueError as exc:
        pytest.skip(str(exc))

    seed = int(os.environ.get(SEED_ENV, "12345"))
    constants = _build_case_constants(target, randomize=randomize, seed=seed)

    CppExporter().export(
        top,
        hpp_file,
        namespace="demo",
        class_name="AdaptiveRoot",
        error_style="exceptions",
    )

    cpp_code = _render_cpp_test(target, constants, seed=seed, randomize=randomize)
    cpp_file.write_text(cpp_code, encoding="utf-8")
    _build_and_run_cpp(case_dir, cpp_file, exe_file)


def test_adaptive_generate_compile_and_run_deterministic() -> None:
    _run_adaptive_case(case_name="adaptive_deterministic", randomize=False)


def test_adaptive_generate_compile_and_run_randomized() -> None:
    _run_adaptive_case(case_name="adaptive_randomized", randomize=True)


@pytest.mark.parametrize(
    "fixture_rdl",
    _fixture_rdl_files(),
    ids=lambda p: _fixture_case_id(p),
)
def test_adaptive_generate_compile_and_run_fixture_rdls(fixture_rdl: Path) -> None:
    case_id = _fixture_case_id(fixture_rdl)
    _run_adaptive_case(
        case_name=f"adaptive_fixture_{case_id}",
        randomize=False,
        source_rdl=fixture_rdl,
    )
