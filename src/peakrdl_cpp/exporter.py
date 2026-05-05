# Copyright (c) 2026 PeakRDL-cpp contributors
# SPDX-License-Identifier: LGPL-3.0-or-later

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from systemrdl.node import AddrmapNode, FieldNode, Node, RegNode, RegfileNode, RootNode
from systemrdl.rdltypes.builtin_enums import AccessType, OnReadType, OnWriteType


CPP_KEYWORDS = {
    "alignas",
    "alignof",
    "and",
    "and_eq",
    "asm",
    "auto",
    "bitand",
    "bitor",
    "bool",
    "break",
    "case",
    "catch",
    "char",
    "char8_t",
    "char16_t",
    "char32_t",
    "class",
    "compl",
    "concept",
    "const",
    "consteval",
    "constexpr",
    "constinit",
    "const_cast",
    "continue",
    "co_await",
    "co_return",
    "co_yield",
    "decltype",
    "default",
    "delete",
    "do",
    "double",
    "dynamic_cast",
    "else",
    "enum",
    "explicit",
    "export",
    "extern",
    "false",
    "float",
    "for",
    "friend",
    "goto",
    "if",
    "inline",
    "int",
    "long",
    "mutable",
    "namespace",
    "new",
    "noexcept",
    "not",
    "not_eq",
    "nullptr",
    "operator",
    "or",
    "or_eq",
    "private",
    "protected",
    "public",
    "register",
    "reinterpret_cast",
    "requires",
    "return",
    "short",
    "signed",
    "sizeof",
    "static",
    "static_assert",
    "static_cast",
    "struct",
    "switch",
    "template",
    "this",
    "thread_local",
    "throw",
    "true",
    "try",
    "typedef",
    "typeid",
    "typename",
    "union",
    "unsigned",
    "using",
    "virtual",
    "void",
    "volatile",
    "wchar_t",
    "while",
    "xor",
    "xor_eq",
}


_CONTAINER_RESERVED = {
    "rd_shadow",
    "wr_shadow",
    "RdShadowOps",
    "WrShadowOps",
    "rd_shadow_read_hw_impl",
    "wr_shadow_flush_impl",
    "wr_shadow_flush_always_impl",
}
_TOP_RESERVED = {
    "rd_shadow",
    "wr_shadow",
    "RdShadowOps",
    "WrShadowOps",
    "ok",
    "last_error",
    "clear_error",
    "base_address",
    "kThrowOnError",
    "rd_shadow_read_hw_impl",
    "wr_shadow_flush_impl",
    "wr_shadow_flush_always_impl",
}
_REG_RESERVED = {
    "rd_shadow",
    "wr_shadow",
    "RdShadowOps",
    "WrShadowOps",
    "read",
    "supports_shadow_write",
    "address",
    "kShadowWriteSupported",
}


@dataclass(frozen=True)
class FieldModel:
    cpp_name: str
    path: str
    lsb: int
    msb: int
    width: int
    mask: int
    sw: AccessType
    onread: Optional[OnReadType]
    onwrite: Optional[OnWriteType]
    singlepulse: bool
    reset: int
    readable: bool
    writable: bool


@dataclass
class RegisterModel:
    cpp_name: str
    class_name: str
    path: str
    width: int
    word_count: int
    reset: int
    read_mask: int
    write_only_mask: int
    rclr_mask: int
    rset_mask: int
    singlepulse_mask: int
    shadow_write_supported: bool
    unsupported_reasons: List[str]
    fields: List[FieldModel]


@dataclass
class ChildSingle:
    cpp_name: str
    offset: int
    target: Union["ContainerModel", RegisterModel]


@dataclass
class ChildArray:
    cpp_name: str
    count: int
    base_offset: int
    stride: int
    element: Union["ContainerModel", RegisterModel]


ChildModel = Union[ChildSingle, ChildArray]


@dataclass
class ContainerModel:
    cpp_name: str
    class_name: str
    path: str
    is_top: bool
    children: List[ChildModel]


@dataclass
class DesignModel:
    namespace: str
    top_class_name: str
    error_style: str
    check_write_range: bool
    access_width: int
    data_type: str
    top: ContainerModel


class CppExporter:
    def export(
        self,
        node: Union[RootNode, AddrmapNode],
        path: Union[str, Path],
        **kwargs: Any,
    ) -> None:
        namespace = kwargs.pop("namespace", None)
        class_name = kwargs.pop("class_name", None)
        error_style = kwargs.pop("error_style", "exceptions")
        check_write_range = kwargs.pop("check_write_range", True)
        if kwargs:
            raise TypeError(f"Unexpected exporter argument: {next(iter(kwargs))}")

        top_node = node.top if isinstance(node, RootNode) else node
        if not isinstance(top_node, AddrmapNode):
            raise TypeError("CppExporter expects a RootNode or AddrmapNode")

        if error_style not in {"exceptions", "status"}:
            raise ValueError("error_style must be one of: exceptions, status")
        if not isinstance(check_write_range, bool):
            raise TypeError("check_write_range must be a bool")

        access_widths = self._collect_access_widths(top_node)
        if len(access_widths) > 1:
            widths = ", ".join(str(v) for v in sorted(access_widths))
            raise ValueError(
                f"Multiple accesswidth values found in design: {widths}. "
                "This exporter currently requires a single uniform accesswidth."
            )

        access_width = next(iter(access_widths), 32)
        if access_width not in {8, 16, 32, 64}:
            raise ValueError(
                f"Unsupported accesswidth={access_width}. "
                "Supported values are 8, 16, 32, or 64."
            )
        self._check_register_address_spans(top_node, access_width)

        namespace_name = _sanitize_identifier(namespace or top_node.inst_name)
        top_class_name = _sanitize_identifier(class_name or _to_class_case(top_node.inst_name))

        builder = _ModelBuilder(top_node, access_width=access_width)
        top_model = builder.build_top()

        design = DesignModel(
            namespace=namespace_name,
            top_class_name=top_class_name,
            error_style=error_style,
            check_write_range=check_write_range,
            access_width=access_width,
            data_type=_cpp_uint_type(access_width),
            top=top_model,
        )

        rendered = _Renderer(design).render()
        Path(path).write_text(rendered, encoding="utf-8")

    @staticmethod
    def _collect_access_widths(top_node: AddrmapNode) -> set[int]:
        widths: set[int] = set()

        def walk(container: Union[AddrmapNode, RegfileNode]) -> None:
            for child in container.children(unroll=False):
                if isinstance(child, RegNode):
                    widths.add(int(child.get_property("accesswidth")))
                elif isinstance(child, (AddrmapNode, RegfileNode)):
                    walk(child)

        walk(top_node)
        return widths

    @staticmethod
    def _check_register_address_spans(top_node: AddrmapNode, access_width: int) -> None:
        access_bytes = access_width // 8
        top_base = int(top_node.absolute_address)

        for node in top_node.descendants(unroll=True):
            if not isinstance(node, RegNode):
                continue

            reg_width = int(node.get_property("regwidth"))
            word_count = _ceil_div(reg_width, access_width)
            rel_addr = int(node.absolute_address) - top_base
            _check_addr_range_fits(
                rel_addr,
                node.get_path(),
                span_bytes=word_count * access_bytes,
                span_desc=f"regwidth={reg_width}, accesswidth={access_width}",
            )


class _ModelBuilder:
    def __init__(self, top_node: AddrmapNode, access_width: int):
        self.top_node = top_node
        self.access_width = access_width
        self.class_name_map: Dict[str, str] = {}
        self.used_class_names: Dict[str, int] = {}

    def build_top(self) -> ContainerModel:
        top_class = self._class_name((self.top_node.inst_name,), "top")
        return self._build_container(
            def_node=self.top_node,
            inst_node=self.top_node,
            def_path=(self.top_node.inst_name,),
            class_name=top_class,
            is_top=True,
        )

    def _build_container(
        self,
        def_node: Union[AddrmapNode, RegfileNode],
        inst_node: Union[AddrmapNode, RegfileNode],
        def_path: Tuple[str, ...],
        class_name: str,
        is_top: bool,
    ) -> ContainerModel:
        children: List[ChildModel] = []
        child_names: List[str] = []

        def_children = list(def_node.children(unroll=False))
        inst_children = list(inst_node.children(unroll=True))
        grouped = self._group_children(inst_children)

        for child_def in def_children:
            child_cpp_name = _sanitize_identifier(child_def.inst_name)
            child_names.append(child_cpp_name)

            key = (type(child_def).__name__, child_def.inst_name)
            inst_list = grouped.get(key, [])
            if not inst_list:
                raise ValueError(
                    f"Internal traversal error: could not locate instance list for '{child_def.get_path()}'"
                )

            if child_def.is_array:
                dims = child_def.array_dimensions
                if len(dims) != 1:
                    raise ValueError(
                        f"Unsupported array rank at '{child_def.get_path()}': {dims}. "
                        "Only one-dimensional arrays are supported."
                    )
                count = dims[0]
                stride = child_def.array_stride

                indexed = [c for c in inst_list if c.current_idx is not None]
                indexed.sort(key=lambda c: c.current_idx[0])
                if len(indexed) != count:
                    raise ValueError(
                        f"Array elaboration mismatch at '{child_def.get_path()}': "
                        f"expected {count} instances, got {len(indexed)}"
                    )
                expected_idx = list(range(count))
                actual_idx = [c.current_idx[0] for c in indexed]
                if actual_idx != expected_idx:
                    raise ValueError(
                        f"Array indexing mismatch at '{child_def.get_path()}': "
                        f"expected indices {expected_idx}, got {actual_idx}"
                    )

                first = indexed[0]
                base_offset = first.absolute_address - inst_node.absolute_address
                _check_addr_range_fits(base_offset, f"{child_def.get_path()}[0]")
                _check_addr_range_fits(stride, f"{child_def.get_path()} stride")
                _check_addr_range_fits(
                    base_offset + (count - 1) * stride,
                    f"{child_def.get_path()} last element offset",
                )

                if isinstance(child_def, RegNode):
                    elem_class = self._class_name(def_path + (child_def.inst_name,), "reg")
                    elem_model = self._build_register(child_def, first, elem_class)
                elif isinstance(child_def, (AddrmapNode, RegfileNode)):
                    elem_class = self._class_name(def_path + (child_def.inst_name,), "blk")
                    elem_model = self._build_container(
                        def_node=child_def,
                        inst_node=first,
                        def_path=def_path + (child_def.inst_name,),
                        class_name=elem_class,
                        is_top=False,
                    )
                else:
                    raise ValueError(
                        f"Unsupported component type '{type(child_def).__name__}' at '{child_def.get_path()}'"
                    )

                children.append(
                    ChildArray(
                        cpp_name=child_cpp_name,
                        count=count,
                        base_offset=base_offset,
                        stride=stride,
                        element=elem_model,
                    )
                )
            else:
                concrete = None
                for candidate in inst_list:
                    if candidate.current_idx is None:
                        concrete = candidate
                        break
                if concrete is None:
                    raise ValueError(
                        f"Internal traversal error: missing non-array instance for '{child_def.get_path()}'"
                    )

                offset = concrete.absolute_address - inst_node.absolute_address
                _check_addr_range_fits(offset, child_def.get_path())
                if isinstance(child_def, RegNode):
                    reg_class = self._class_name(def_path + (child_def.inst_name,), "reg")
                    target = self._build_register(child_def, concrete, reg_class)
                elif isinstance(child_def, (AddrmapNode, RegfileNode)):
                    blk_class = self._class_name(def_path + (child_def.inst_name,), "blk")
                    target = self._build_container(
                        def_node=child_def,
                        inst_node=concrete,
                        def_path=def_path + (child_def.inst_name,),
                        class_name=blk_class,
                        is_top=False,
                    )
                else:
                    raise ValueError(
                        f"Unsupported component type '{type(child_def).__name__}' at '{child_def.get_path()}'"
                    )

                children.append(ChildSingle(cpp_name=child_cpp_name, offset=offset, target=target))

        self._check_name_conflicts(
            scope_path=inst_node.get_path(),
            names=child_names,
            reserved=_TOP_RESERVED if is_top else _CONTAINER_RESERVED,
        )

        return ContainerModel(
            cpp_name=_sanitize_identifier(inst_node.inst_name),
            class_name=class_name,
            path=inst_node.get_path(),
            is_top=is_top,
            children=children,
        )

    def _build_register(self, def_reg: RegNode, inst_reg: RegNode, class_name: str) -> RegisterModel:
        reg_width = int(def_reg.get_property("regwidth"))
        if reg_width > 64:
            raise ValueError(
                f"Register '{def_reg.get_path()}' has width {reg_width}. "
                "This exporter currently supports register widths up to 64 bits."
            )
        word_count = _ceil_div(reg_width, self.access_width)
        buffer_reads = _optional_bool_property(def_reg, "buffer_reads")
        buffer_writes = _optional_bool_property(def_reg, "buffer_writes")

        fields: List[FieldModel] = []
        field_names: List[str] = []

        reset = 0
        read_mask = 0
        write_only_mask = 0
        rclr_mask = 0
        rset_mask = 0
        singlepulse_mask = 0
        shadow_write_supported = True
        unsupported_reasons: List[str] = []
        has_readable_field = False
        has_writable_field = False

        for item in def_reg.fields(skip_not_present=True):
            if not isinstance(item, FieldNode):
                continue
            field = item

            cpp_name = _sanitize_identifier(field.inst_name)
            field_names.append(cpp_name)

            sw = field.get_property("sw")
            if not isinstance(sw, AccessType):
                raise ValueError(f"Unable to evaluate sw access type at '{field.get_path()}'")

            onread = field.get_property("onread")
            if onread is not None and not isinstance(onread, OnReadType):
                raise ValueError(f"Unsupported onread value at '{field.get_path()}': {onread}")

            onwrite = field.get_property("onwrite")
            if onwrite is not None and not isinstance(onwrite, OnWriteType):
                raise ValueError(f"Unsupported onwrite value at '{field.get_path()}': {onwrite}")

            if onread == OnReadType.ruser:
                raise ValueError(f"Unsupported onread=ruser at '{field.get_path()}'")

            swwe = bool(field.get_property("swwe"))
            swwel = bool(field.get_property("swwel"))
            intr = bool(field.get_property("intr"))
            if swwe or swwel:
                raise ValueError(f"Unsupported swwe/swwel at '{field.get_path()}'")
            if intr:
                raise ValueError(f"Unsupported interrupt field at '{field.get_path()}'")

            singlepulse = bool(field.get_property("singlepulse"))

            if onwrite is not None:
                shadow_write_supported = False
                unsupported_reasons.append(
                    f"{field.get_path()}: onwrite={onwrite.name} is unsupported for shadow write operations"
                )

            mask = _field_mask(field)
            reset_value = int(field.get_property("reset") or 0)
            reset |= (reset_value << field.lsb) & mask

            readable = _is_sw_readable(sw)
            writable = _is_sw_writable(sw)
            if onwrite is not None:
                # onwrite side effects are not modeled for direct SW writes yet.
                # Do not emit field write APIs to avoid semantically incorrect behavior.
                writable = False
            has_readable_field = has_readable_field or readable
            has_writable_field = has_writable_field or writable

            if reg_width > self.access_width and _field_crosses_access_word(field, self.access_width):
                if readable and not buffer_reads:
                    raise ValueError(
                        f"Field '{field.get_path()}' crosses accesswidth={self.access_width} word boundaries. "
                        f"Readable crossing fields require buffer_reads=true on register '{def_reg.get_path()}'."
                    )
                if writable and not buffer_writes:
                    raise ValueError(
                        f"Field '{field.get_path()}' crosses accesswidth={self.access_width} word boundaries. "
                        f"Writable crossing fields require buffer_writes=true on register '{def_reg.get_path()}'."
                    )

            if readable:
                read_mask |= mask
            if sw in {AccessType.w, AccessType.w1}:
                write_only_mask |= mask
            if onread == OnReadType.rclr:
                rclr_mask |= mask
            if onread == OnReadType.rset:
                rset_mask |= mask
            if singlepulse:
                singlepulse_mask |= mask

            fields.append(
                FieldModel(
                    cpp_name=cpp_name,
                    path=field.get_path(),
                    lsb=field.lsb,
                    msb=field.msb,
                    width=field.width,
                    mask=mask,
                    sw=sw,
                    onread=onread,
                    onwrite=onwrite,
                    singlepulse=singlepulse,
                    reset=reset_value,
                    readable=readable,
                    writable=writable,
                )
            )

        self._check_name_conflicts(
            scope_path=inst_reg.get_path(),
            names=field_names,
            reserved=_REG_RESERVED,
        )

        fields.sort(key=lambda f: f.lsb)
        if reg_width < 64:
            reset &= (1 << reg_width) - 1
        if reg_width > self.access_width:
            if has_readable_field and not buffer_reads:
                raise ValueError(
                    f"Register '{def_reg.get_path()}' has regwidth {reg_width} greater than accesswidth "
                    f"{self.access_width}. Readable multiword registers require buffer_reads=true."
                )
            if has_writable_field and not buffer_writes:
                raise ValueError(
                    f"Register '{def_reg.get_path()}' has regwidth {reg_width} greater than accesswidth "
                    f"{self.access_width}. Writable multiword registers require buffer_writes=true."
                )

        return RegisterModel(
            cpp_name=_sanitize_identifier(inst_reg.inst_name),
            class_name=class_name,
            path=inst_reg.get_path(),
            width=reg_width,
            word_count=word_count,
            reset=reset,
            read_mask=read_mask,
            write_only_mask=write_only_mask,
            rclr_mask=rclr_mask,
            rset_mask=rset_mask,
            singlepulse_mask=singlepulse_mask,
            shadow_write_supported=shadow_write_supported,
            unsupported_reasons=unsupported_reasons,
            fields=fields,
        )

    @staticmethod
    def _group_children(children: Sequence[Node]) -> Dict[Tuple[str, str], List[Node]]:
        grouped: Dict[Tuple[str, str], List[Node]] = {}
        for child in children:
            key = (type(child).__name__, child.inst_name)
            grouped.setdefault(key, []).append(child)
        return grouped

    @staticmethod
    def _check_name_conflicts(scope_path: str, names: List[str], reserved: set[str]) -> None:
        seen: set[str] = set()
        for name in names:
            if name in reserved:
                raise ValueError(
                    f"Name conflict at '{scope_path}': '{name}' conflicts with reserved generated API symbol"
                )
            if name in seen:
                raise ValueError(
                    f"Name conflict at '{scope_path}': duplicate generated C++ member '{name}'"
                )
            seen.add(name)

    def _class_name(self, def_path: Tuple[str, ...], suffix: str) -> str:
        key = "::".join(def_path + (suffix,))
        if key in self.class_name_map:
            return self.class_name_map[key]

        raw = _to_class_case("_".join(def_path + (suffix,)))
        candidate = _sanitize_identifier(raw)
        if candidate in self.used_class_names:
            self.used_class_names[candidate] += 1
            candidate = f"{candidate}{self.used_class_names[candidate]}"
        else:
            self.used_class_names[candidate] = 0

        self.class_name_map[key] = candidate
        return candidate


class _Renderer:
    def __init__(self, design: DesignModel):
        self.design = design

    def render(self) -> str:
        lines: List[str] = []
        throw_on_error = self.design.error_style == "exceptions"

        lines.append("/*")
        lines.append(" * Generated by PeakRDL-cpp. Do not edit manually.")
        lines.append(" *")
        lines.append(" * SPDX-License-Identifier: CC0-1.0")
        lines.append(" */")
        lines.append("")
        lines.append("#pragma once")
        lines.append("")
        lines.append("#include <cassert>")
        lines.append("#include <array>")
        lines.append("#include <cstddef>")
        lines.append("#include <cstdint>")
        lines.append("#include <limits>")
        lines.append("#include <memory>")
        lines.append("#include <stdexcept>")
        lines.append("#include <string>")
        lines.append("#include <type_traits>")
        lines.append("#include <utility>")
        lines.append("")
        lines.append(f"namespace {self.design.namespace} {{")
        lines.append("")
        lines.append("using addr_t = std::uint32_t;")
        lines.append(f"using data_t = {self.design.data_type};")
        lines.append(f"static constexpr std::uint8_t kAccessWidth = {self.design.access_width};")
        lines.append(
            f"static constexpr bool kCheckWriteRange = {'true' if self.design.check_write_range else 'false'};"
        )
        lines.append("")
        lines.append("// Bus adapter contract (documented-only):")
        lines.append("//   data_t read(addr_t addr);")
        lines.append("//   void write(addr_t addr, data_t value);")
        lines.append("")

        self._emit_detail_runtime(lines)

        reg_models = self._collect_register_models(self.design.top)
        for reg in reg_models:
            self._emit_register_class(lines, reg)

        container_models = self._collect_container_models(self.design.top)
        for container in container_models:
            self._emit_container_class(lines, container)

        self._emit_top_class(lines, self.design.top, throw_on_error)

        lines.append(f"}} // namespace {self.design.namespace}")
        lines.append("")
        return "\n".join(lines)

    def _emit_detail_runtime(self, lines: List[str]) -> None:
        lines.append("namespace detail {")
        lines.append("")
        lines.append("enum class AccessMode : std::uint8_t { na, r, w, rw, rw1, w1 };")
        lines.append("using signed_data_t = std::make_signed_t<data_t>;")
        lines.append("using signed_input_t = std::intmax_t;")
        lines.append("using unsigned_input_t = std::uintmax_t;")
        lines.append("")
        lines.append("template <typename BusT, bool ThrowOnError>")
        lines.append("class Context {")
        lines.append("public:")
        lines.append("    explicit Context(BusT& bus_ref, addr_t base)")
        lines.append("        : bus(&bus_ref), base_address(base), ok_flag(true), last_error_msg() {}")
        lines.append("")
        lines.append("    void fail(const char* message) {")
        lines.append("        if constexpr (ThrowOnError) {")
        lines.append("            throw std::runtime_error(message);")
        lines.append("        } else {")
        lines.append("            ok_flag = false;")
        lines.append("            last_error_msg = message;")
        lines.append("        }")
        lines.append("    }")
        lines.append("")
        lines.append("    bool ok() const { return ok_flag; }")
        lines.append("    const std::string& last_error() const { return last_error_msg; }")
        lines.append("    void clear_error() {")
        lines.append("        ok_flag = true;")
        lines.append("        last_error_msg.clear();")
        lines.append("    }")
        lines.append("")
        lines.append("    BusT* bus;")
        lines.append("    addr_t base_address;")
        lines.append("")
        lines.append("private:")
        lines.append("    bool ok_flag;")
        lines.append("    std::string last_error_msg;")
        lines.append("};")
        lines.append("")
        lines.append("static constexpr std::size_t kAccessBytes = kAccessWidth / 8;")
        lines.append("static constexpr std::size_t kMaxRegWords = (64 + kAccessWidth - 1) / kAccessWidth;")
        lines.append("using reg_storage_t = std::array<data_t, kMaxRegWords>;")
        lines.append("")
        lines.append("inline data_t bitmask_for_width(std::uint8_t width) {")
        lines.append("    if (width >= kAccessWidth) {")
        lines.append("        return std::numeric_limits<data_t>::max();")
        lines.append("    }")
        lines.append("    return static_cast<data_t>((data_t{1} << width) - data_t{1});")
        lines.append("}")
        lines.append("")
        lines.append("template <typename ElemT, std::size_t N>")
        lines.append("class NodeArray {")
        lines.append("public:")
        lines.append("    ElemT& operator[](std::size_t idx) {")
        lines.append("        assert(idx < N);")
        lines.append("        assert(items_[idx] != nullptr);")
        lines.append("        return *items_[idx];")
        lines.append("    }")
        lines.append("    const ElemT& operator[](std::size_t idx) const {")
        lines.append("        assert(idx < N);")
        lines.append("        assert(items_[idx] != nullptr);")
        lines.append("        return *items_[idx];")
        lines.append("    }")
        lines.append("    constexpr std::size_t size() const { return N; }")
        lines.append("    void set(std::size_t idx, std::unique_ptr<ElemT> elem) {")
        lines.append("        assert(idx < N);")
        lines.append("        items_[idx] = std::move(elem);")
        lines.append("    }")
        lines.append("private:")
        lines.append("    std::array<std::unique_ptr<ElemT>, N> items_{};")
        lines.append("};")
        lines.append("")
        lines.append("template <typename BusT, bool ThrowOnError>")
        lines.append("class RegisterState {")
        lines.append("public:")
        lines.append("    RegisterState(")
        lines.append("        Context<BusT, ThrowOnError>& ctx,")
        lines.append("        addr_t reg_offset,")
        lines.append("        std::uint8_t reg_width,")
        lines.append("        std::uint8_t word_count,")
        lines.append("        reg_storage_t reset_value,")
        lines.append("        reg_storage_t read_mask,")
        lines.append("        reg_storage_t write_only_mask,")
        lines.append("        reg_storage_t rclr_mask,")
        lines.append("        reg_storage_t rset_mask,")
        lines.append("        reg_storage_t singlepulse_mask,")
        lines.append("        bool shadow_write_supported)")
        lines.append("        : ctx_(ctx),")
        lines.append("          offset_(reg_offset),")
        lines.append("          reg_width_(reg_width),")
        lines.append("          word_count_(word_count),")
        lines.append("          read_shadow_(reset_value),")
        lines.append("          write_shadow_(reset_value),")
        lines.append("          read_mask_(read_mask),")
        lines.append("          write_only_mask_(write_only_mask),")
        lines.append("          rclr_mask_(rclr_mask),")
        lines.append("          rset_mask_(rset_mask),")
        lines.append("          singlepulse_mask_(singlepulse_mask),")
        lines.append("          shadow_write_supported_(shadow_write_supported),")
        lines.append("          dirty_(false) {}")
        lines.append("")
        lines.append("    addr_t address() const { return static_cast<addr_t>(ctx_.base_address + offset_); }")
        lines.append("    bool dirty() const { return dirty_; }")
        lines.append("    bool supports_shadow_write() const { return shadow_write_supported_; }")
        lines.append("")
        lines.append("    reg_storage_t read_hw() {")
        lines.append("        reg_storage_t raw{};")
        lines.append("        for (std::uint8_t idx = 0; idx < word_count_; ++idx) {")
        lines.append("            raw[idx] = to_bus_data(ctx_.bus->read(word_address(idx)), idx);")
        lines.append("        }")
        lines.append("        apply_hw_read(raw);")
        lines.append("        return raw;")
        lines.append("    }")
        lines.append("")
        lines.append("    data_t read_hw_scalar() {")
        lines.append("        return read_hw()[0];")
        lines.append("    }")
        lines.append("")
        lines.append("    void shadow_read_hw() {")
        lines.append("        (void)read_hw();")
        lines.append("    }")
        lines.append("")
        lines.append("    void flush() {")
        lines.append("        flush_impl(true);")
        lines.append("    }")
        lines.append("")
        lines.append("    void flush_always() {")
        lines.append("        flush_impl(false);")
        lines.append("    }")
        lines.append("")
        lines.append("    data_t read_field_hw_scalar(std::uint8_t lsb, std::uint8_t width) {")
        lines.append("        return extract_scalar(read_hw(), lsb, width);")
        lines.append("    }")
        lines.append("")
        lines.append("    data_t read_field_shadow_scalar(std::uint8_t lsb, std::uint8_t width) const {")
        lines.append("        return extract_scalar(read_shadow_, lsb, width);")
        lines.append("    }")
        lines.append("")
        lines.append("    data_t read_field_staged_scalar(std::uint8_t lsb, std::uint8_t width) const {")
        lines.append("        return extract_scalar(write_shadow_, lsb, width);")
        lines.append("    }")
        lines.append("")
        lines.append("    template <std::size_t WordCount>")
        lines.append("    std::array<data_t, WordCount> read_field_hw_array(std::uint8_t lsb, std::uint8_t width) {")
        lines.append("        return extract_array<WordCount>(read_hw(), lsb, width);")
        lines.append("    }")
        lines.append("")
        lines.append("    template <std::size_t WordCount>")
        lines.append("    std::array<data_t, WordCount> read_field_shadow_array(std::uint8_t lsb, std::uint8_t width) const {")
        lines.append("        return extract_array<WordCount>(read_shadow_, lsb, width);")
        lines.append("    }")
        lines.append("")
        lines.append("    template <std::size_t WordCount>")
        lines.append("    std::array<data_t, WordCount> read_field_staged_array(std::uint8_t lsb, std::uint8_t width) const {")
        lines.append("        return extract_array<WordCount>(write_shadow_, lsb, width);")
        lines.append("    }")
        lines.append("")
        lines.append("    void shadow_write_unsigned(")
        lines.append("        std::uint8_t lsb,")
        lines.append("        std::uint8_t width,")
        lines.append("        unsigned_input_t value,")
        lines.append("        const char* field_name) {")
        lines.append("        if (!shadow_write_supported_) {")
        lines.append("            ctx_.fail(\"field.wr_shadow.write() is unsupported for this register\");")
        lines.append("            return;")
        lines.append("        }")
        lines.append("        const data_t limit = bitmask_for_width(width);")
        lines.append("        if constexpr (kCheckWriteRange) {")
        lines.append("            if (value > static_cast<unsigned_input_t>(limit)) {")
        lines.append("                ctx_.fail(field_name);")
        lines.append("                return;")
        lines.append("            }")
        lines.append("        }")
        lines.append("        const data_t narrowed = static_cast<data_t>(value);")
        lines.append("        reg_storage_t next = write_shadow_;")
        lines.append("        insert_scalar(next, lsb, width, narrowed);")
        lines.append("        if (next != write_shadow_) {")
        lines.append("            write_shadow_ = next;")
        lines.append("            dirty_ = true;")
        lines.append("        }")
        lines.append("    }")
        lines.append("")
        lines.append("    template <std::size_t WordCount>")
        lines.append("    void shadow_write_array(")
        lines.append("        std::uint8_t lsb,")
        lines.append("        std::uint8_t width,")
        lines.append("        const std::array<data_t, WordCount>& value,")
        lines.append("        const char* field_name) {")
        lines.append("        if (!shadow_write_supported_) {")
        lines.append("            ctx_.fail(\"field.wr_shadow.write() is unsupported for this register\");")
        lines.append("            return;")
        lines.append("        }")
        lines.append("        if constexpr (kCheckWriteRange) {")
        lines.append("            if (!check_array<WordCount>(width, value)) {")
        lines.append("                ctx_.fail(field_name);")
        lines.append("                return;")
        lines.append("            }")
        lines.append("        }")
        lines.append("        reg_storage_t next = write_shadow_;")
        lines.append("        insert_array(next, lsb, width, value);")
        lines.append("        if (next != write_shadow_) {")
        lines.append("            write_shadow_ = next;")
        lines.append("            dirty_ = true;")
        lines.append("        }")
        lines.append("    }")
        lines.append("")
        lines.append("    void shadow_write_signed(")
        lines.append("        std::uint8_t lsb,")
        lines.append("        std::uint8_t width,")
        lines.append("        signed_input_t value,")
        lines.append("        const char* field_name) {")
        lines.append("        if (!shadow_write_supported_) {")
        lines.append("            ctx_.fail(\"field.wr_shadow.write() is unsupported for this register\");")
        lines.append("            return;")
        lines.append("        }")
        lines.append("        if constexpr (kCheckWriteRange) {")
        lines.append("            if (!check_signed(width, value)) {")
        lines.append("                ctx_.fail(field_name);")
        lines.append("                return;")
        lines.append("            }")
        lines.append("        }")
        lines.append("        const data_t encoded_value = encode_signed(width, value);")
        lines.append("        shadow_write_unsigned(lsb, width, encoded_value, field_name);")
        lines.append("    }")
        lines.append("")
        lines.append("    void direct_write_unsigned(")
        lines.append("        std::uint8_t lsb,")
        lines.append("        std::uint8_t width,")
        lines.append("        unsigned_input_t value,")
        lines.append("        AccessMode sw_mode,")
        lines.append("        bool singlepulse,")
        lines.append("        const char* field_name) {")
        lines.append("        const data_t limit = bitmask_for_width(width);")
        lines.append("        if constexpr (kCheckWriteRange) {")
        lines.append("            if (value > static_cast<unsigned_input_t>(limit)) {")
        lines.append("                ctx_.fail(field_name);")
        lines.append("                return;")
        lines.append("            }")
        lines.append("        }")
        lines.append("")
        lines.append("        reg_storage_t base_value{};")
        lines.append("        if (sw_mode == AccessMode::w || sw_mode == AccessMode::w1) {")
        lines.append("            base_value = write_shadow_;")
        lines.append("        } else {")
        lines.append("            const reg_storage_t hw_value = read_hw();")
        lines.append("            base_value = merge_read_with_write_only(hw_value);")
        lines.append("        }")
        lines.append("")
        lines.append("        const data_t narrowed = static_cast<data_t>(value);")
        lines.append("        insert_scalar(base_value, lsb, width, narrowed);")
        lines.append("        write_hw(base_value);")
        lines.append("        insert_scalar(write_shadow_, lsb, width, narrowed);")
        lines.append("        insert_scalar(read_shadow_, lsb, width, narrowed);")
        lines.append("        dirty_ = false;")
        lines.append("        if (singlepulse) {")
        lines.append("            clear_range(write_shadow_, lsb, width);")
        lines.append("            clear_range(read_shadow_, lsb, width);")
        lines.append("        }")
        lines.append("    }")
        lines.append("")
        lines.append("    template <std::size_t WordCount>")
        lines.append("    void direct_write_array(")
        lines.append("        std::uint8_t lsb,")
        lines.append("        std::uint8_t width,")
        lines.append("        const std::array<data_t, WordCount>& value,")
        lines.append("        AccessMode sw_mode,")
        lines.append("        bool singlepulse,")
        lines.append("        const char* field_name) {")
        lines.append("        if constexpr (kCheckWriteRange) {")
        lines.append("            if (!check_array<WordCount>(width, value)) {")
        lines.append("                ctx_.fail(field_name);")
        lines.append("                return;")
        lines.append("            }")
        lines.append("        }")
        lines.append("        reg_storage_t base_value{};")
        lines.append("        if (sw_mode == AccessMode::w || sw_mode == AccessMode::w1) {")
        lines.append("            base_value = write_shadow_;")
        lines.append("        } else {")
        lines.append("            const reg_storage_t hw_value = read_hw();")
        lines.append("            base_value = merge_read_with_write_only(hw_value);")
        lines.append("        }")
        lines.append("        insert_array(base_value, lsb, width, value);")
        lines.append("        write_hw(base_value);")
        lines.append("        insert_array(write_shadow_, lsb, width, value);")
        lines.append("        insert_array(read_shadow_, lsb, width, value);")
        lines.append("        dirty_ = false;")
        lines.append("        if (singlepulse) {")
        lines.append("            clear_range(write_shadow_, lsb, width);")
        lines.append("            clear_range(read_shadow_, lsb, width);")
        lines.append("        }")
        lines.append("    }")
        lines.append("")
        lines.append("    void direct_write_signed(")
        lines.append("        std::uint8_t lsb,")
        lines.append("        std::uint8_t width,")
        lines.append("        signed_input_t value,")
        lines.append("        AccessMode sw_mode,")
        lines.append("        bool singlepulse,")
        lines.append("        const char* field_name) {")
        lines.append("        if constexpr (kCheckWriteRange) {")
        lines.append("            if (!check_signed(width, value)) {")
        lines.append("                ctx_.fail(field_name);")
        lines.append("                return;")
        lines.append("            }")
        lines.append("        }")
        lines.append("        const data_t encoded = encode_signed(width, value);")
        lines.append("        direct_write_unsigned(lsb, width, encoded, sw_mode, singlepulse, field_name);")
        lines.append("    }")
        lines.append("")
        lines.append("private:")
        lines.append("    void flush_impl(bool only_if_dirty) {")
        lines.append("        if (!shadow_write_supported_) {")
        lines.append("            ctx_.fail(\"wr_shadow.flush() is unsupported for this register\");")
        lines.append("            return;")
        lines.append("        }")
        lines.append("        if (only_if_dirty && !dirty_) {")
        lines.append("            return;")
        lines.append("        }")
        lines.append("        write_hw(write_shadow_);")
        lines.append("        dirty_ = false;")
        lines.append("        apply_singlepulse_clear();")
        lines.append("    }")
        lines.append("    void apply_hw_read(const reg_storage_t& raw) {")
        lines.append("        for (std::uint8_t idx = 0; idx < word_count_; ++idx) {")
        lines.append("            const data_t readable = read_mask_[idx];")
        lines.append("            read_shadow_[idx] = static_cast<data_t>((read_shadow_[idx] & static_cast<data_t>(~readable)) | (raw[idx] & readable));")
        lines.append("            write_shadow_[idx] = static_cast<data_t>((write_shadow_[idx] & static_cast<data_t>(~readable)) | (raw[idx] & readable));")
        lines.append("            write_shadow_[idx] = static_cast<data_t>((write_shadow_[idx] & static_cast<data_t>(~rclr_mask_[idx])) | rset_mask_[idx]);")
        lines.append("            read_shadow_[idx] &= word_mask(idx);")
        lines.append("            write_shadow_[idx] &= word_mask(idx);")
        lines.append("        }")
        lines.append("    }")
        lines.append("")
        lines.append("    static bool check_signed(std::uint8_t width, signed_input_t value) {")
        lines.append("        if (width == 0) {")
        lines.append("            return false;")
        lines.append("        }")
        lines.append("        if (width >= kAccessWidth) {")
        lines.append(
            "            const signed_input_t min_value = static_cast<signed_input_t>(std::numeric_limits<signed_data_t>::min());"
        )
        lines.append(
            "            const signed_input_t max_value = static_cast<signed_input_t>(std::numeric_limits<signed_data_t>::max());"
        )
        lines.append("            return value >= min_value && value <= max_value;")
        lines.append("        }")
        lines.append("        const data_t sign_bit = static_cast<data_t>(data_t{1} << (width - 1));")
        lines.append("        const signed_input_t min_value = -static_cast<signed_input_t>(sign_bit);")
        lines.append(
            "        const signed_input_t max_value = static_cast<signed_input_t>(sign_bit - data_t{1});"
        )
        lines.append("        return value >= min_value && value <= max_value;")
        lines.append("    }")
        lines.append("")
        lines.append("    static data_t encode_signed(std::uint8_t width, signed_input_t value) {")
        lines.append("        const data_t limit = bitmask_for_width(width);")
        lines.append("        return static_cast<data_t>(static_cast<data_t>(value) & limit);")
        lines.append("    }")
        lines.append("")
        lines.append("    template <std::size_t WordCount>")
        lines.append("    static bool check_array(std::uint8_t width, const std::array<data_t, WordCount>& value) {")
        lines.append("        for (std::size_t idx = 0; idx < WordCount; ++idx) {")
        lines.append("            const std::uint8_t used = word_width_for_field_word(width, idx);")
        lines.append("            if (used == 0 && value[idx] != data_t{0}) {")
        lines.append("                return false;")
        lines.append("            }")
        lines.append("            if (used > 0 && used < kAccessWidth && (value[idx] & static_cast<data_t>(~bitmask_for_width(used))) != data_t{0}) {")
        lines.append("                return false;")
        lines.append("            }")
        lines.append("        }")
        lines.append("        return true;")
        lines.append("    }")
        lines.append("")
        lines.append("    static std::uint8_t word_width_for_field_word(std::uint8_t width, std::size_t idx) {")
        lines.append("        const std::size_t consumed = idx * kAccessWidth;")
        lines.append("        if (consumed >= width) {")
        lines.append("            return 0;")
        lines.append("        }")
        lines.append("        const std::size_t remaining = width - consumed;")
        lines.append("        return static_cast<std::uint8_t>(remaining > kAccessWidth ? kAccessWidth : remaining);")
        lines.append("    }")
        lines.append("")
        lines.append("    data_t to_bus_data(data_t value, std::uint8_t word_idx) const {")
        lines.append("        return static_cast<data_t>(value & word_mask(word_idx));")
        lines.append("    }")
        lines.append("")
        lines.append("    data_t word_mask(std::uint8_t word_idx) const {")
        lines.append("        const std::uint16_t consumed = static_cast<std::uint16_t>(word_idx) * kAccessWidth;")
        lines.append("        if (consumed >= reg_width_) {")
        lines.append("            return data_t{0};")
        lines.append("        }")
        lines.append("        const std::uint16_t remaining = static_cast<std::uint16_t>(reg_width_ - consumed);")
        lines.append("        return bitmask_for_width(static_cast<std::uint8_t>(remaining > kAccessWidth ? kAccessWidth : remaining));")
        lines.append("    }")
        lines.append("")
        lines.append("    addr_t word_address(std::uint8_t word_idx) const {")
        lines.append("        return static_cast<addr_t>(address() + static_cast<addr_t>(word_idx * kAccessBytes));")
        lines.append("    }")
        lines.append("")
        lines.append("    void write_hw(const reg_storage_t& value) {")
        lines.append("        for (std::uint8_t idx = 0; idx < word_count_; ++idx) {")
        lines.append("            ctx_.bus->write(word_address(idx), to_bus_data(value[idx], idx));")
        lines.append("        }")
        lines.append("    }")
        lines.append("")
        lines.append("    reg_storage_t merge_read_with_write_only(const reg_storage_t& hw_value) const {")
        lines.append("        reg_storage_t out{};")
        lines.append("        for (std::uint8_t idx = 0; idx < word_count_; ++idx) {")
        lines.append("            out[idx] = static_cast<data_t>((hw_value[idx] & static_cast<data_t>(~write_only_mask_[idx])) | (write_shadow_[idx] & write_only_mask_[idx]));")
        lines.append("            out[idx] &= word_mask(idx);")
        lines.append("        }")
        lines.append("        return out;")
        lines.append("    }")
        lines.append("")
        lines.append("    static data_t extract_scalar(const reg_storage_t& src, std::uint8_t lsb, std::uint8_t width) {")
        lines.append("        data_t out = 0;")
        lines.append("        std::uint8_t remaining = width;")
        lines.append("        std::uint8_t bit = lsb;")
        lines.append("        std::uint8_t out_shift = 0;")
        lines.append("        while (remaining > 0) {")
        lines.append("            const std::uint8_t word = bit / kAccessWidth;")
        lines.append("            const std::uint8_t bit_in_word = bit % kAccessWidth;")
        lines.append("            const std::uint8_t available = static_cast<std::uint8_t>(kAccessWidth - bit_in_word);")
        lines.append("            const std::uint8_t chunk = remaining < available ? remaining : available;")
        lines.append("            const data_t chunk_value = static_cast<data_t>((src[word] >> bit_in_word) & bitmask_for_width(chunk));")
        lines.append("            out = static_cast<data_t>(out | static_cast<data_t>(chunk_value << out_shift));")
        lines.append("            remaining = static_cast<std::uint8_t>(remaining - chunk);")
        lines.append("            bit = static_cast<std::uint8_t>(bit + chunk);")
        lines.append("            out_shift = static_cast<std::uint8_t>(out_shift + chunk);")
        lines.append("        }")
        lines.append("        return out;")
        lines.append("    }")
        lines.append("")
        lines.append("    template <std::size_t WordCount>")
        lines.append("    static std::array<data_t, WordCount> extract_array(const reg_storage_t& src, std::uint8_t lsb, std::uint8_t width) {")
        lines.append("        std::array<data_t, WordCount> out{};")
        lines.append("        for (std::size_t idx = 0; idx < WordCount; ++idx) {")
        lines.append("            const std::uint8_t chunk_width = word_width_for_field_word(width, idx);")
        lines.append("            if (chunk_width != 0) {")
        lines.append("                out[idx] = extract_scalar(src, static_cast<std::uint8_t>(lsb + idx * kAccessWidth), chunk_width);")
        lines.append("            }")
        lines.append("        }")
        lines.append("        return out;")
        lines.append("    }")
        lines.append("")
        lines.append("    static void insert_scalar(reg_storage_t& dst, std::uint8_t lsb, std::uint8_t width, data_t value) {")
        lines.append("        std::uint8_t remaining = width;")
        lines.append("        std::uint8_t bit = lsb;")
        lines.append("        std::uint8_t in_shift = 0;")
        lines.append("        while (remaining > 0) {")
        lines.append("            const std::uint8_t word = bit / kAccessWidth;")
        lines.append("            const std::uint8_t bit_in_word = bit % kAccessWidth;")
        lines.append("            const std::uint8_t available = static_cast<std::uint8_t>(kAccessWidth - bit_in_word);")
        lines.append("            const std::uint8_t chunk = remaining < available ? remaining : available;")
        lines.append("            const data_t chunk_mask = bitmask_for_width(chunk);")
        lines.append("            const data_t word_mask_value = static_cast<data_t>(chunk_mask << bit_in_word);")
        lines.append("            const data_t chunk_value = static_cast<data_t>(((value >> in_shift) & chunk_mask) << bit_in_word);")
        lines.append("            dst[word] = static_cast<data_t>((dst[word] & static_cast<data_t>(~word_mask_value)) | chunk_value);")
        lines.append("            remaining = static_cast<std::uint8_t>(remaining - chunk);")
        lines.append("            bit = static_cast<std::uint8_t>(bit + chunk);")
        lines.append("            in_shift = static_cast<std::uint8_t>(in_shift + chunk);")
        lines.append("        }")
        lines.append("    }")
        lines.append("")
        lines.append("    template <std::size_t WordCount>")
        lines.append("    static void insert_array(reg_storage_t& dst, std::uint8_t lsb, std::uint8_t width, const std::array<data_t, WordCount>& value) {")
        lines.append("        for (std::size_t idx = 0; idx < WordCount; ++idx) {")
        lines.append("            const std::uint8_t chunk_width = word_width_for_field_word(width, idx);")
        lines.append("            if (chunk_width != 0) {")
        lines.append("                insert_scalar(dst, static_cast<std::uint8_t>(lsb + idx * kAccessWidth), chunk_width, value[idx]);")
        lines.append("            }")
        lines.append("        }")
        lines.append("    }")
        lines.append("")
        lines.append("    static void clear_range(reg_storage_t& dst, std::uint8_t lsb, std::uint8_t width) {")
        lines.append("        insert_scalar(dst, lsb, width, data_t{0});")
        lines.append("    }")
        lines.append("")
        lines.append("    void apply_singlepulse_clear() {")
        lines.append("        for (std::uint8_t idx = 0; idx < word_count_; ++idx) {")
        lines.append("            write_shadow_[idx] = static_cast<data_t>(write_shadow_[idx] & static_cast<data_t>(~singlepulse_mask_[idx]));")
        lines.append("            read_shadow_[idx] = static_cast<data_t>(read_shadow_[idx] & static_cast<data_t>(~singlepulse_mask_[idx]));")
        lines.append("        }")
        lines.append("    }")
        lines.append("")
        lines.append("    Context<BusT, ThrowOnError>& ctx_;")
        lines.append("    addr_t offset_;")
        lines.append("    std::uint8_t reg_width_;")
        lines.append("    std::uint8_t word_count_;")
        lines.append("    reg_storage_t read_shadow_;")
        lines.append("    reg_storage_t write_shadow_;")
        lines.append("    reg_storage_t read_mask_;")
        lines.append("    reg_storage_t write_only_mask_;")
        lines.append("    reg_storage_t rclr_mask_;")
        lines.append("    reg_storage_t rset_mask_;")
        lines.append("    reg_storage_t singlepulse_mask_;")
        lines.append("    bool shadow_write_supported_;")
        lines.append("    bool dirty_;")
        lines.append("};")
        lines.append("")
        lines.append("} // namespace detail")
        lines.append("")

    def _collect_register_models(self, top: ContainerModel) -> List[RegisterModel]:
        out: List[RegisterModel] = []
        seen: set[str] = set()

        def walk(container: ContainerModel) -> None:
            for child in container.children:
                if isinstance(child, ChildSingle):
                    target = child.target
                    if isinstance(target, RegisterModel):
                        if target.class_name not in seen:
                            seen.add(target.class_name)
                            out.append(target)
                    else:
                        walk(target)
                else:
                    target = child.element
                    if isinstance(target, RegisterModel):
                        if target.class_name not in seen:
                            seen.add(target.class_name)
                            out.append(target)
                    else:
                        walk(target)

        walk(top)
        return out

    def _collect_container_models(self, top: ContainerModel) -> List[ContainerModel]:
        out: List[ContainerModel] = []
        seen: set[str] = set()

        def walk(container: ContainerModel) -> None:
            for child in container.children:
                target = child.target if isinstance(child, ChildSingle) else child.element
                if isinstance(target, ContainerModel):
                    if target.class_name not in seen:
                        seen.add(target.class_name)
                        walk(target)
                        out.append(target)

        walk(top)
        return out

    def _emit_register_class(self, lines: List[str], reg: RegisterModel) -> None:
        lines.append("template <typename BusT, bool ThrowOnError>")
        lines.append(f"class {reg.class_name} {{")
        lines.append("public:")
        lines.append(
            f"    static constexpr bool kShadowWriteSupported = {'true' if reg.shadow_write_supported else 'false'};"
        )
        lines.append("")
        lines.append(
            f"    explicit {reg.class_name}(detail::Context<BusT, ThrowOnError>& ctx, addr_t reg_offset)"
        )

        state_ctor = (
            "state_(ctx, reg_offset, "
            + str(reg.width)
            + ", "
            + str(reg.word_count)
            + ", "
            + _word_array_lit(reg.reset, reg.word_count, self.design.access_width)
            + ", "
            + _word_array_lit(reg.read_mask, reg.word_count, self.design.access_width)
            + ", "
            + _word_array_lit(reg.write_only_mask, reg.word_count, self.design.access_width)
            + ", "
            + _word_array_lit(reg.rclr_mask, reg.word_count, self.design.access_width)
            + ", "
            + _word_array_lit(reg.rset_mask, reg.word_count, self.design.access_width)
            + ", "
            + _word_array_lit(reg.singlepulse_mask, reg.word_count, self.design.access_width)
            + ", kShadowWriteSupported"
            + ")"
        )
        init_parts = [state_ctor, "rd_shadow(this)", "wr_shadow(this)"]
        init_parts.extend(f"{field.cpp_name}(this)" for field in reg.fields)

        lines.append("        : " + init_parts[0])
        for p in init_parts[1:]:
            lines.append("        , " + p)
        lines.append("    {}")
        lines.append("")

        if reg.word_count == 1:
            lines.append("    data_t read() { return state_.read_hw_scalar(); }")
        else:
            lines.append("    detail::reg_storage_t read() { return state_.read_hw(); }")
        lines.append("    bool supports_shadow_write() const { return kShadowWriteSupported; }")
        lines.append("    addr_t address() const { return state_.address(); }")
        lines.append("")

        lines.append("    class RdShadowOps {")
        lines.append("    public:")
        lines.append(f"        explicit RdShadowOps({reg.class_name}* owner) : owner_(owner) {{}}")
        lines.append("        void read_hw() { owner_->state_.shadow_read_hw(); }")
        lines.append("    private:")
        lines.append(f"        {reg.class_name}* owner_;")
        lines.append("    };")
        lines.append("")
        lines.append("    class WrShadowOps {")
        lines.append("    public:")
        lines.append(f"        explicit WrShadowOps({reg.class_name}* owner) : owner_(owner) {{}}")
        lines.append("        void flush() { owner_->state_.flush(); }")
        lines.append("        void flush_always() { owner_->state_.flush_always(); }")
        lines.append("        bool dirty() const { return owner_->state_.dirty(); }")
        lines.append("    private:")
        lines.append(f"        {reg.class_name}* owner_;")
        lines.append("    };")
        lines.append("")
        lines.append("    RdShadowOps rd_shadow;")
        lines.append("    WrShadowOps wr_shadow;")
        lines.append("")

        for field in reg.fields:
            self._emit_field_class(lines, reg, field)
            lines.append(f"    {self._field_class_name(reg, field)} {field.cpp_name};")
            lines.append("")

        if reg.unsupported_reasons:
            lines.append("    // Shadow write operations are disabled for this register:")
            for reason in reg.unsupported_reasons:
                lines.append(f"    // - {reason}")
            lines.append("")

        lines.append("private:")
        lines.append("    detail::RegisterState<BusT, ThrowOnError> state_;")
        lines.append("};")
        lines.append("")

    def _emit_field_class(self, lines: List[str], reg: RegisterModel, field: FieldModel) -> None:
        cls = self._field_class_name(reg, field)
        field_word_count = _ceil_div(field.width, self.design.access_width)
        scalar_field = field.width <= self.design.access_width
        lines.append(f"    class {cls} {{")
        lines.append("    public:")
        lines.append(f"        static constexpr std::uint8_t LSB = {field.lsb};")
        lines.append(f"        static constexpr std::uint8_t MSB = {field.msb};")
        lines.append(f"        static constexpr std::uint8_t WIDTH = {field.width};")
        if not scalar_field:
            lines.append(f"        static constexpr std::size_t WORD_COUNT = {field_word_count};")
        lines.append(
            f"        static constexpr detail::AccessMode SW = detail::AccessMode::{_sw_cpp(field.sw)};"
        )
        lines.append(
            f"        static constexpr bool SINGLEPULSE = {'true' if field.singlepulse else 'false'};"
        )
        lines.append("")
        lines.append(
            f"        explicit {cls}({reg.class_name}* owner) : owner_(owner), rd_shadow(this), wr_shadow(this) {{}}"
        )
        lines.append("")

        if field.readable:
            if scalar_field:
                lines.append("        data_t read() {")
                lines.append("            return owner_->state_.read_field_hw_scalar(LSB, WIDTH);")
                lines.append("        }")
            else:
                lines.append("        std::array<data_t, WORD_COUNT> read() {")
                lines.append("            return owner_->state_.template read_field_hw_array<WORD_COUNT>(LSB, WIDTH);")
                lines.append("        }")
            lines.append("")

        if field.writable:
            if scalar_field:
                lines.append("        template <typename IntT>")
                lines.append("        void write(IntT value) {")
                lines.append(
                    "            static_assert(std::is_integral_v<IntT> && !std::is_same_v<std::remove_cv_t<IntT>, bool>,"
                )
                lines.append('                "write() requires an integral type (excluding bool)");')
                lines.append("            if constexpr (std::is_signed_v<IntT>) {")
                lines.append(
                    "                owner_->state_.direct_write_signed(LSB, WIDTH, static_cast<detail::signed_input_t>(value), SW, SINGLEPULSE, "
                    + _c_string(field.path)
                    + ");"
                )
                lines.append("            } else {")
                lines.append(
                    "                owner_->state_.direct_write_unsigned(LSB, WIDTH, static_cast<detail::unsigned_input_t>(value), SW, SINGLEPULSE, "
                    + _c_string(field.path)
                    + ");"
                )
                lines.append("            }")
                lines.append("        }")
            else:
                lines.append("        void write(const std::array<data_t, WORD_COUNT>& value) {")
                lines.append(
                    "            owner_->state_.template direct_write_array<WORD_COUNT>(LSB, WIDTH, value, SW, SINGLEPULSE, "
                    + _c_string(field.path)
                    + ");"
                )
                lines.append("        }")
            lines.append("")

        lines.append("        class RdShadowOps {")
        lines.append("        public:")
        lines.append(f"            explicit RdShadowOps({cls}* owner) : owner_(owner) {{}}")
        if scalar_field:
            lines.append("            data_t read() const {")
            lines.append("                return owner_->owner_->state_.read_field_shadow_scalar(LSB, WIDTH);")
            lines.append("            }")
        else:
            lines.append("            std::array<data_t, WORD_COUNT> read() const {")
            lines.append("                return owner_->owner_->state_.template read_field_shadow_array<WORD_COUNT>(LSB, WIDTH);")
            lines.append("            }")
        lines.append("        private:")
        lines.append(f"            {cls}* owner_;")
        lines.append("        };")
        lines.append("")
        lines.append("        class WrShadowOps {")
        lines.append("        public:")
        lines.append(f"            explicit WrShadowOps({cls}* owner) : owner_(owner) {{}}")
        if scalar_field:
            lines.append("            data_t read() const {")
            lines.append("                return owner_->owner_->state_.read_field_staged_scalar(LSB, WIDTH);")
            lines.append("            }")
        else:
            lines.append("            std::array<data_t, WORD_COUNT> read() const {")
            lines.append("                return owner_->owner_->state_.template read_field_staged_array<WORD_COUNT>(LSB, WIDTH);")
            lines.append("            }")
        if field.writable:
            if scalar_field:
                lines.append("            template <typename IntT>")
                lines.append("            void write(IntT value) {")
                lines.append(
                    "                static_assert(std::is_integral_v<IntT> && !std::is_same_v<std::remove_cv_t<IntT>, bool>,"
                )
                lines.append(
                    '                    "wr_shadow.write() requires an integral type (excluding bool)");'
                )
                lines.append("                if constexpr (std::is_signed_v<IntT>) {")
                lines.append(
                    "                    owner_->owner_->state_.shadow_write_signed(LSB, WIDTH, static_cast<detail::signed_input_t>(value), "
                    + _c_string(field.path)
                    + ");"
                )
                lines.append("                } else {")
                lines.append(
                    "                    owner_->owner_->state_.shadow_write_unsigned(LSB, WIDTH, static_cast<detail::unsigned_input_t>(value), "
                    + _c_string(field.path)
                    + ");"
                )
                lines.append("                }")
                lines.append("            }")
            else:
                lines.append("            void write(const std::array<data_t, WORD_COUNT>& value) {")
                lines.append(
                    "                owner_->owner_->state_.template shadow_write_array<WORD_COUNT>(LSB, WIDTH, value, "
                    + _c_string(field.path)
                    + ");"
                )
                lines.append("            }")
        lines.append("        private:")
        lines.append(f"            {cls}* owner_;")
        lines.append("        };")
        lines.append("")
        lines.append("        RdShadowOps rd_shadow;")
        lines.append("        WrShadowOps wr_shadow;")
        lines.append("")
        lines.append("    private:")
        lines.append(f"        {reg.class_name}* owner_;")
        lines.append("    };")

    def _emit_container_class(self, lines: List[str], container: ContainerModel) -> None:
        lines.append("template <typename BusT, bool ThrowOnError>")
        lines.append(f"class {container.class_name} {{")
        lines.append("private:")
        lines.append("    detail::Context<BusT, ThrowOnError>& ctx_;")
        lines.append("    addr_t instance_base_offset_;")
        lines.append("public:")

        lines.append(
            f"    explicit {container.class_name}(detail::Context<BusT, ThrowOnError>& ctx, addr_t instance_base_offset)"
        )

        initializers = ["ctx_(ctx)", "instance_base_offset_(instance_base_offset)"]
        initializers.extend(self._single_child_initializers(container, throw_expr="ThrowOnError"))
        initializers.append("rd_shadow(this)")
        initializers.append("wr_shadow(this)")

        lines.append(f"        : {initializers[0]}")
        for init in initializers[1:]:
            lines.append(f"        , {init}")
        lines.append("    {")
        for line in self._array_setup_lines(container, throw_expr="ThrowOnError"):
            lines.append("        " + line)
        lines.append("    }")
        lines.append("")

        self._emit_rd_shadow_block(lines, container, top=False)
        self._emit_wr_shadow_block(lines, container, top=False)

        for child in container.children:
            lines.append(self._child_member_decl(child, throw_expr="ThrowOnError"))
        lines.append("")

        lines.append("private:")
        lines.append("    void rd_shadow_read_hw_impl() {")
        self._emit_rd_shadow_read_body(lines, container, top=False)
        lines.append("    }")
        lines.append("")
        lines.append("    void wr_shadow_flush_impl() {")
        self._emit_wr_shadow_flush_body(lines, container, top=False, always=False)
        lines.append("    }")
        lines.append("")
        lines.append("    void wr_shadow_flush_always_impl() {")
        self._emit_wr_shadow_flush_body(lines, container, top=False, always=True)
        lines.append("    }")
        lines.append("};")
        lines.append("")

    def _emit_top_class(self, lines: List[str], top: ContainerModel, throw_on_error: bool) -> None:
        lines.append("template <typename BusT>")
        lines.append(f"class {self.design.top_class_name} {{")
        lines.append("public:")
        lines.append(
            f"    static constexpr bool kThrowOnError = {'true' if throw_on_error else 'false'};"
        )
        lines.append("")
        lines.append("private:")
        lines.append("    using ContextT = detail::Context<BusT, kThrowOnError>;")
        lines.append("    ContextT ctx_;")
        lines.append("    addr_t instance_base_offset_;")
        lines.append("public:")
        lines.append("")

        lines.append(
            f"    explicit {self.design.top_class_name}(BusT& bus, addr_t base_address = 0)"
        )
        initializers = ["ctx_(bus, base_address)", "instance_base_offset_(0)"]
        initializers.extend(self._single_child_initializers(top, throw_expr="kThrowOnError"))
        initializers.append("rd_shadow(this)")
        initializers.append("wr_shadow(this)")

        lines.append(f"        : {initializers[0]}")
        for init in initializers[1:]:
            lines.append(f"        , {init}")
        lines.append("    {")
        for line in self._array_setup_lines(top, throw_expr="kThrowOnError"):
            lines.append("        " + line)
        lines.append("    }")
        lines.append("")
        lines.append("    bool ok() const { return ctx_.ok(); }")
        lines.append("    const std::string& last_error() const { return ctx_.last_error(); }")
        lines.append("    void clear_error() { ctx_.clear_error(); }")
        lines.append("    addr_t base_address() const { return ctx_.base_address; }")
        lines.append("")

        self._emit_rd_shadow_block(lines, top, top=True)
        self._emit_wr_shadow_block(lines, top, top=True)

        for child in top.children:
            lines.append(self._child_member_decl(child, throw_expr="kThrowOnError"))
        lines.append("")

        lines.append("private:")
        lines.append("    void rd_shadow_read_hw_impl() {")
        self._emit_rd_shadow_read_body(lines, top, top=True)
        lines.append("    }")
        lines.append("")
        lines.append("    void wr_shadow_flush_impl() {")
        self._emit_wr_shadow_flush_body(lines, top, top=True, always=False)
        lines.append("    }")
        lines.append("")
        lines.append("    void wr_shadow_flush_always_impl() {")
        self._emit_wr_shadow_flush_body(lines, top, top=True, always=True)
        lines.append("    }")
        lines.append("};")
        lines.append("")

    @staticmethod
    def _field_class_name(reg: RegisterModel, field: FieldModel) -> str:
        return _sanitize_identifier(f"{reg.class_name}_{field.cpp_name}_Field")

    def _child_member_decl(self, child: ChildModel, throw_expr: str) -> str:
        if isinstance(child, ChildSingle):
            tname = self._target_type(child.target, throw_expr)
            return f"    {tname} {child.cpp_name};"

        elem_type = self._target_type(child.element, throw_expr)
        return f"    detail::NodeArray<{elem_type}, {child.count}> {child.cpp_name};"

    def _target_type(self, target: Union[ContainerModel, RegisterModel], throw_expr: str) -> str:
        return f"{target.class_name}<BusT, {throw_expr}>"

    def _single_child_initializers(self, container: ContainerModel, throw_expr: str) -> List[str]:
        out: List[str] = []
        for child in container.children:
            if isinstance(child, ChildSingle):
                out.append(
                    f"{child.cpp_name}(ctx_, static_cast<addr_t>(instance_base_offset_ + {_addr_lit(child.offset)}))"
                )
        return out

    def _array_setup_lines(self, container: ContainerModel, throw_expr: str) -> List[str]:
        out: List[str] = []
        for child in container.children:
            if not isinstance(child, ChildArray):
                continue
            elem_type = self._target_type(child.element, throw_expr)
            for idx in range(child.count):
                offset = child.base_offset + idx * child.stride
                out.append(
                    f"{child.cpp_name}.set({idx}u, std::make_unique<{elem_type}>(ctx_, static_cast<addr_t>(instance_base_offset_ + {_addr_lit(offset)})));"
                )
        return out

    def _emit_rd_shadow_block(self, lines: List[str], container: ContainerModel, top: bool) -> None:
        owner_type = self.design.top_class_name if top else container.class_name
        lines.append("    class RdShadowOps {")
        lines.append("    public:")
        lines.append(f"        explicit RdShadowOps({owner_type}* owner) : owner_(owner) {{}}")
        lines.append("        void read_hw() { owner_->rd_shadow_read_hw_impl(); }")
        lines.append("    private:")
        lines.append(f"        {owner_type}* owner_;")
        lines.append("    };")
        lines.append("")
        lines.append("    RdShadowOps rd_shadow;")
        lines.append("")

    def _emit_wr_shadow_block(self, lines: List[str], container: ContainerModel, top: bool) -> None:
        owner_type = self.design.top_class_name if top else container.class_name
        lines.append("    class WrShadowOps {")
        lines.append("    public:")
        lines.append(f"        explicit WrShadowOps({owner_type}* owner) : owner_(owner) {{}}")
        lines.append("        void flush() { owner_->wr_shadow_flush_impl(); }")
        lines.append("        void flush_always() { owner_->wr_shadow_flush_always_impl(); }")
        lines.append("    private:")
        lines.append(f"        {owner_type}* owner_;")
        lines.append("    };")
        lines.append("")
        lines.append("    WrShadowOps wr_shadow;")
        lines.append("")

    def _emit_rd_shadow_read_body(self, lines: List[str], container: ContainerModel, top: bool) -> None:
        for child in container.children:
            if isinstance(child, ChildSingle):
                target = child.target
                if isinstance(target, RegisterModel):
                    lines.append(
                        f"        if ({child.cpp_name}.supports_shadow_write()) {child.cpp_name}.rd_shadow.read_hw();"
                    )
                else:
                    lines.append(f"        {child.cpp_name}.rd_shadow.read_hw();")
            else:
                if isinstance(child.element, RegisterModel):
                    lines.append(f"        for (std::size_t i = 0; i < {child.cpp_name}.size(); ++i) {{")
                    lines.append(f"            auto& elem = {child.cpp_name}[i];")
                    lines.append("            if (elem.supports_shadow_write()) elem.rd_shadow.read_hw();")
                    lines.append("        }")
                else:
                    lines.append(f"        for (std::size_t i = 0; i < {child.cpp_name}.size(); ++i) {{")
                    lines.append(f"            auto& elem = {child.cpp_name}[i];")
                    lines.append("            elem.rd_shadow.read_hw();")
                    lines.append("        }")

    def _emit_wr_shadow_flush_body(
        self,
        lines: List[str],
        container: ContainerModel,
        top: bool,
        always: bool,
    ) -> None:
        flush_call = "flush_always" if always else "flush"
        for child in container.children:
            if isinstance(child, ChildSingle):
                target = child.target
                if isinstance(target, RegisterModel):
                    lines.append(
                        f"        if ({child.cpp_name}.supports_shadow_write()) {child.cpp_name}.wr_shadow.{flush_call}();"
                    )
                else:
                    lines.append(f"        {child.cpp_name}.wr_shadow.{flush_call}();")
            else:
                if isinstance(child.element, RegisterModel):
                    lines.append(f"        for (std::size_t i = 0; i < {child.cpp_name}.size(); ++i) {{")
                    lines.append(f"            auto& elem = {child.cpp_name}[i];")
                    lines.append(
                        f"            if (elem.supports_shadow_write()) elem.wr_shadow.{flush_call}();"
                    )
                    lines.append("        }")
                else:
                    lines.append(f"        for (std::size_t i = 0; i < {child.cpp_name}.size(); ++i) {{")
                    lines.append(f"            auto& elem = {child.cpp_name}[i];")
                    lines.append(f"            elem.wr_shadow.{flush_call}();")
                    lines.append("        }")


def _sanitize_identifier(name: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "unnamed"
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    if cleaned in CPP_KEYWORDS or keyword.iskeyword(cleaned):
        cleaned = f"{cleaned}_"
    return cleaned


def _check_addr_range_fits(
    start_addr: int,
    context: str,
    *,
    span_bytes: int = 1,
    span_desc: Optional[str] = None,
) -> None:
    if span_bytes < 1:
        raise ValueError(f"Invalid address span at '{context}': span_bytes must be >= 1")

    max_addr = (1 << 32) - 1
    max_start = max_addr - (span_bytes - 1)
    if start_addr < 0 or start_addr > max_start:
        if span_bytes == 1:
            raise ValueError(
                f"Address/offset overflow at '{context}': value 0x{start_addr:X} does not fit addr_t (std::uint32_t)."
            )

        end_addr = start_addr + span_bytes - 1
        span_detail = span_desc if span_desc is not None else f"span_bytes={span_bytes}"
        raise ValueError(
            f"Address span overflow at '{context}': {span_detail} requires bytes [0x{start_addr:X}..0x{end_addr:X}], "
            "which does not fit addr_t (std::uint32_t)."
        )


def _to_class_case(name: str) -> str:
    parts = [p for p in re.split(r"[^0-9a-zA-Z]+", name) if p]
    if not parts:
        return "RegisterMap"
    return "".join(p[0].upper() + p[1:] for p in parts)


def _data_lit(value: int) -> str:
    return f"static_cast<data_t>(0x{value:X}ull)"


def _word_array_lit(value: int, word_count: int, access_width: int) -> str:
    word_mask = (1 << access_width) - 1
    words = [
        _data_lit((value >> (idx * access_width)) & word_mask)
        for idx in range(word_count)
    ]
    return "detail::reg_storage_t{" + ", ".join(words) + "}"


def _addr_lit(value: int) -> str:
    return f"0x{value:X}u"


def _cpp_uint_type(width: int) -> str:
    mapping = {
        8: "std::uint8_t",
        16: "std::uint16_t",
        32: "std::uint32_t",
        64: "std::uint64_t",
    }
    return mapping[width]


def _field_mask(field: FieldNode) -> int:
    if field.width >= 64:
        raw = (1 << 64) - 1
    else:
        raw = (1 << field.width) - 1
    return raw << field.lsb


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def _field_crosses_access_word(field: FieldNode, access_width: int) -> bool:
    return (int(field.lsb) // access_width) != (int(field.msb) // access_width)


def _optional_bool_property(node: Node, name: str) -> bool:
    try:
        return bool(node.get_property(name))
    except LookupError:
        return False


def _is_sw_readable(sw: AccessType) -> bool:
    return sw in {AccessType.r, AccessType.rw, AccessType.rw1}


def _is_sw_writable(sw: AccessType) -> bool:
    return sw in {AccessType.w, AccessType.rw, AccessType.w1, AccessType.rw1}


def _sw_cpp(sw: AccessType) -> str:
    mapping = {
        AccessType.na: "na",
        AccessType.r: "r",
        AccessType.w: "w",
        AccessType.rw: "rw",
        AccessType.rw1: "rw1",
        AccessType.w1: "w1",
    }
    return mapping[sw]


def _c_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
