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


_CONTAINER_RESERVED = {"shadow", "ShadowOps", "shadow_read_hw_impl", "shadow_flush_impl"}
_TOP_RESERVED = {
    "shadow",
    "ShadowOps",
    "ok",
    "last_error",
    "clear_error",
    "base_address",
    "kThrowOnError",
    "shadow_read_hw_impl",
    "shadow_flush_impl",
}
_REG_RESERVED = {
    "shadow",
    "ShadowOps",
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

    @staticmethod
    def _check_addr_fits(value: int, context: str) -> None:
        max_addr = (1 << 32) - 1
        if value < 0 or value > max_addr:
            raise ValueError(
                f"Address/offset overflow at '{context}': value 0x{value:X} does not fit addr_t (std::uint32_t)."
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
                self._check_addr_fits(base_offset, f"{child_def.get_path()}[0]")
                self._check_addr_fits(stride, f"{child_def.get_path()} stride")
                self._check_addr_fits(
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
                self._check_addr_fits(offset, child_def.get_path())
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
        if reg_width > self.access_width:
            raise ValueError(
                f"Register '{def_reg.get_path()}' has regwidth {reg_width}, "
                f"which is larger than deduced accesswidth {self.access_width}. "
                "This exporter currently requires regwidth <= accesswidth."
            )

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

        return RegisterModel(
            cpp_name=_sanitize_identifier(inst_reg.inst_name),
            class_name=class_name,
            path=inst_reg.get_path(),
            width=reg_width,
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
        lines.append("enum class OnReadMode : std::uint8_t { none, rclr, rset, ruser };")
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
        lines.append("    ElemT& operator[](std::size_t idx) { return *items_[idx]; }")
        lines.append("    const ElemT& operator[](std::size_t idx) const { return *items_[idx]; }")
        lines.append("    constexpr std::size_t size() const { return N; }")
        lines.append("    void set(std::size_t idx, std::unique_ptr<ElemT> elem) { items_[idx] = std::move(elem); }")
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
        lines.append("        data_t reset_value,")
        lines.append("        data_t read_mask,")
        lines.append("        data_t write_only_mask,")
        lines.append("        data_t rclr_mask,")
        lines.append("        data_t rset_mask,")
        lines.append("        data_t singlepulse_mask,")
        lines.append("        bool shadow_write_supported,")
        lines.append("        const char* reg_name)")
        lines.append("        : ctx_(ctx),")
        lines.append("          offset_(reg_offset),")
        lines.append("          read_shadow_(reset_value),")
        lines.append("          write_shadow_(reset_value),")
        lines.append("          read_mask_(read_mask),")
        lines.append("          write_only_mask_(write_only_mask),")
        lines.append("          rclr_mask_(rclr_mask),")
        lines.append("          rset_mask_(rset_mask),")
        lines.append("          singlepulse_mask_(singlepulse_mask),")
        lines.append("          shadow_write_supported_(shadow_write_supported),")
        lines.append("          dirty_(false),")
        lines.append("          name_(reg_name) {}")
        lines.append("")
        lines.append("    addr_t address() const { return static_cast<addr_t>(ctx_.base_address + offset_); }")
        lines.append("    bool dirty() const { return dirty_; }")
        lines.append("    bool supports_shadow_write() const { return shadow_write_supported_; }")
        lines.append("")
        lines.append("    data_t read_hw() {")
        lines.append("        data_t raw = ctx_.bus->read(address());")
        lines.append("        apply_hw_read(raw);")
        lines.append("        return raw;")
        lines.append("    }")
        lines.append("")
        lines.append("    void shadow_read_hw() {")
        lines.append("        (void)read_hw();")
        lines.append("    }")
        lines.append("")
        lines.append("    void flush() {")
        lines.append("        if (!shadow_write_supported_) {")
        lines.append("            ctx_.fail(\"shadow.flush() is unsupported for this register\");")
        lines.append("            return;")
        lines.append("        }")
        lines.append("        if (!dirty_) {")
        lines.append("            return;")
        lines.append("        }")
        lines.append("        ctx_.bus->write(address(), to_bus_data(write_shadow_));")
        lines.append("        dirty_ = false;")
        lines.append("        apply_singlepulse_clear();")
        lines.append("    }")
        lines.append("")
        lines.append("    data_t read_field_hw(data_t mask, std::uint8_t lsb, const char* field_name) {")
        lines.append("        (void)field_name;")
        lines.append("        data_t raw = read_hw();")
        lines.append("        return static_cast<data_t>((raw & mask) >> lsb);")
        lines.append("    }")
        lines.append("")
        lines.append("    data_t read_field_shadow(data_t mask, std::uint8_t lsb) const {")
        lines.append("        return static_cast<data_t>((write_shadow_ & mask) >> lsb);")
        lines.append("    }")
        lines.append("")
        lines.append("    void shadow_write_unsigned(")
        lines.append("        data_t mask,")
        lines.append("        std::uint8_t lsb,")
        lines.append("        std::uint8_t width,")
        lines.append("        unsigned_input_t value,")
        lines.append("        const char* field_name) {")
        lines.append("        if (!shadow_write_supported_) {")
        lines.append("            ctx_.fail(\"field.shadow.write() is unsupported for this register\");")
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
        lines.append("        const data_t encoded = static_cast<data_t>((narrowed << lsb) & mask);")
        lines.append(
            "        const data_t next = static_cast<data_t>((write_shadow_ & static_cast<data_t>(~mask)) | encoded);"
        )
        lines.append("        if (next != write_shadow_) {")
        lines.append("            write_shadow_ = next;")
        lines.append("            dirty_ = true;")
        lines.append("        }")
        lines.append(
            "        read_shadow_ = static_cast<data_t>((read_shadow_ & static_cast<data_t>(~mask)) | encoded);"
        )
        lines.append("    }")
        lines.append("")
        lines.append("    void shadow_write_signed(")
        lines.append("        data_t mask,")
        lines.append("        std::uint8_t lsb,")
        lines.append("        std::uint8_t width,")
        lines.append("        signed_input_t value,")
        lines.append("        const char* field_name) {")
        lines.append("        if (!shadow_write_supported_) {")
        lines.append("            ctx_.fail(\"field.shadow.write() is unsupported for this register\");")
        lines.append("            return;")
        lines.append("        }")
        lines.append("        if constexpr (kCheckWriteRange) {")
        lines.append("            if (!check_signed(width, value)) {")
        lines.append("                ctx_.fail(field_name);")
        lines.append("                return;")
        lines.append("            }")
        lines.append("        }")
        lines.append("        const data_t encoded_value = encode_signed(width, value);")
        lines.append("        shadow_write_unsigned(mask, lsb, width, encoded_value, field_name);")
        lines.append("    }")
        lines.append("")
        lines.append("    void direct_write_unsigned(")
        lines.append("        data_t mask,")
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
        lines.append("        data_t base_value = 0;")
        lines.append("        if (sw_mode == AccessMode::w || sw_mode == AccessMode::w1) {")
        lines.append("            base_value = write_shadow_;")
        lines.append("        } else {")
        lines.append("            base_value = read_hw();")
        lines.append("        }")
        lines.append("")
        lines.append("        const data_t narrowed = static_cast<data_t>(value);")
        lines.append("        const data_t encoded = static_cast<data_t>((narrowed << lsb) & mask);")
        lines.append(
            "        const data_t next = static_cast<data_t>((base_value & static_cast<data_t>(~mask)) | encoded);"
        )
        lines.append("        ctx_.bus->write(address(), to_bus_data(next));")
        lines.append(
            "        write_shadow_ = static_cast<data_t>((write_shadow_ & static_cast<data_t>(~mask)) | encoded);"
        )
        lines.append(
            "        read_shadow_ = static_cast<data_t>((read_shadow_ & static_cast<data_t>(~mask)) | encoded);"
        )
        lines.append("        dirty_ = false;")
        lines.append("        if (singlepulse) {")
        lines.append("            write_shadow_ &= static_cast<data_t>(~mask);")
        lines.append("            read_shadow_ &= static_cast<data_t>(~mask);")
        lines.append("        }")
        lines.append("    }")
        lines.append("")
        lines.append("    void direct_write_signed(")
        lines.append("        data_t mask,")
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
        lines.append("        direct_write_unsigned(mask, lsb, width, encoded, sw_mode, singlepulse, field_name);")
        lines.append("    }")
        lines.append("")
        lines.append("private:")
        lines.append("    void apply_hw_read(data_t raw) {")
        lines.append("        const data_t readable = read_mask_;")
        lines.append(
            "        read_shadow_ = static_cast<data_t>((read_shadow_ & static_cast<data_t>(~readable)) | (raw & readable));"
        )
        lines.append(
            "        write_shadow_ = static_cast<data_t>((write_shadow_ & static_cast<data_t>(~readable)) | (raw & readable));"
        )
        lines.append("        write_shadow_ &= static_cast<data_t>(~rclr_mask_);")
        lines.append("        write_shadow_ |= rset_mask_;")
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
        lines.append("    static data_t to_bus_data(data_t value) {")
        lines.append("        return static_cast<data_t>(value & bitmask_for_width(kAccessWidth));")
        lines.append("    }")
        lines.append("")
        lines.append("    void apply_singlepulse_clear() {")
        lines.append("        if (singlepulse_mask_ == 0) {")
        lines.append("            return;")
        lines.append("        }")
        lines.append("        write_shadow_ &= static_cast<data_t>(~singlepulse_mask_);")
        lines.append("        read_shadow_ &= static_cast<data_t>(~singlepulse_mask_);")
        lines.append("    }")
        lines.append("")
        lines.append("    Context<BusT, ThrowOnError>& ctx_;")
        lines.append("    addr_t offset_;")
        lines.append("    data_t read_shadow_;")
        lines.append("    data_t write_shadow_;")
        lines.append("    data_t read_mask_;")
        lines.append("    data_t write_only_mask_;")
        lines.append("    data_t rclr_mask_;")
        lines.append("    data_t rset_mask_;")
        lines.append("    data_t singlepulse_mask_;")
        lines.append("    bool shadow_write_supported_;")
        lines.append("    bool dirty_;")
        lines.append("    const char* name_;")
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
            + _data_lit(reg.reset)
            + ", "
            + _data_lit(reg.read_mask)
            + ", "
            + _data_lit(reg.write_only_mask)
            + ", "
            + _data_lit(reg.rclr_mask)
            + ", "
            + _data_lit(reg.rset_mask)
            + ", "
            + _data_lit(reg.singlepulse_mask)
            + ", kShadowWriteSupported, "
            + _c_string(reg.path)
            + ")"
        )
        init_parts = [state_ctor, "shadow(this)"]
        init_parts.extend(f"{field.cpp_name}(this)" for field in reg.fields)

        lines.append("        : " + init_parts[0])
        for p in init_parts[1:]:
            lines.append("        , " + p)
        lines.append("    {}")
        lines.append("")

        lines.append("    data_t read() { return state_.read_hw(); }")
        lines.append("    bool supports_shadow_write() const { return kShadowWriteSupported; }")
        lines.append("    addr_t address() const { return state_.address(); }")
        lines.append("")

        lines.append("    class ShadowOps {")
        lines.append("    public:")
        lines.append(f"        explicit ShadowOps({reg.class_name}* owner) : owner_(owner) {{}}")
        lines.append("        void read_hw() { owner_->state_.shadow_read_hw(); }")
        lines.append("        void flush() { owner_->state_.flush(); }")
        lines.append("        bool dirty() const { return owner_->state_.dirty(); }")
        lines.append("    private:")
        lines.append(f"        {reg.class_name}* owner_;")
        lines.append("    };")
        lines.append("")
        lines.append("    ShadowOps shadow;")
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
        lines.append(f"    class {cls} {{")
        lines.append("    public:")
        lines.append(f"        static constexpr std::uint8_t LSB = {field.lsb};")
        lines.append(f"        static constexpr std::uint8_t MSB = {field.msb};")
        lines.append(f"        static constexpr std::uint8_t WIDTH = {field.width};")
        lines.append(f"        static constexpr data_t MASK = {_data_lit(field.mask)};")
        lines.append(
            f"        static constexpr detail::AccessMode SW = detail::AccessMode::{_sw_cpp(field.sw)};"
        )
        lines.append(
            f"        static constexpr detail::OnReadMode ONREAD = detail::OnReadMode::{_onread_cpp(field.onread)};"
        )
        lines.append(
            f"        static constexpr bool SINGLEPULSE = {'true' if field.singlepulse else 'false'};"
        )
        lines.append("")
        lines.append(f"        explicit {cls}({reg.class_name}* owner) : owner_(owner), shadow(this) {{}}")
        lines.append("")

        if field.readable:
            lines.append("        data_t read() {")
            lines.append(
                f"            return owner_->state_.read_field_hw(MASK, LSB, {_c_string(field.path)});"
            )
            lines.append("        }")
            lines.append("")

        if field.writable:
            lines.append("        template <typename IntT>")
            lines.append("        void write(IntT value) {")
            lines.append(
                "            static_assert(std::is_integral_v<IntT> && !std::is_same_v<std::remove_cv_t<IntT>, bool>,"
            )
            lines.append('                "write() requires an integral type (excluding bool)");')
            lines.append("            if constexpr (std::is_signed_v<IntT>) {")
            lines.append(
                "                owner_->state_.direct_write_signed(MASK, LSB, WIDTH, static_cast<detail::signed_input_t>(value), SW, SINGLEPULSE, "
                + _c_string(field.path)
                + ");"
            )
            lines.append("            } else {")
            lines.append(
                "                owner_->state_.direct_write_unsigned(MASK, LSB, WIDTH, static_cast<detail::unsigned_input_t>(value), SW, SINGLEPULSE, "
                + _c_string(field.path)
                + ");"
            )
            lines.append("            }")
            lines.append("        }")
            lines.append("")

        lines.append("        class ShadowOps {")
        lines.append("        public:")
        lines.append(f"            explicit ShadowOps({cls}* owner) : owner_(owner) {{}}")
        lines.append("            data_t read() const {")
        lines.append("                return owner_->owner_->state_.read_field_shadow(MASK, LSB);")
        lines.append("            }")
        if field.writable:
            lines.append("            template <typename IntT>")
            lines.append("            void write(IntT value) {")
            lines.append(
                "                static_assert(std::is_integral_v<IntT> && !std::is_same_v<std::remove_cv_t<IntT>, bool>,"
            )
            lines.append(
                '                    "shadow.write() requires an integral type (excluding bool)");'
            )
            lines.append("                if constexpr (std::is_signed_v<IntT>) {")
            lines.append(
                "                    owner_->owner_->state_.shadow_write_signed(MASK, LSB, WIDTH, static_cast<detail::signed_input_t>(value), "
                + _c_string(field.path)
                + ");"
            )
            lines.append("                } else {")
            lines.append(
                "                    owner_->owner_->state_.shadow_write_unsigned(MASK, LSB, WIDTH, static_cast<detail::unsigned_input_t>(value), "
                + _c_string(field.path)
                + ");"
            )
            lines.append("                }")
            lines.append("            }")
        lines.append("        private:")
        lines.append(f"            {cls}* owner_;")
        lines.append("        };")
        lines.append("")
        lines.append("        ShadowOps shadow;")
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
        initializers.append("shadow(this)")

        lines.append(f"        : {initializers[0]}")
        for init in initializers[1:]:
            lines.append(f"        , {init}")
        lines.append("    {")
        for line in self._array_setup_lines(container, throw_expr="ThrowOnError"):
            lines.append("        " + line)
        lines.append("    }")
        lines.append("")

        self._emit_shadow_block(lines, container, top=False)

        for child in container.children:
            lines.append(self._child_member_decl(child, throw_expr="ThrowOnError"))
        lines.append("")

        lines.append("private:")
        lines.append("    void shadow_read_hw_impl() {")
        self._emit_shadow_read_body(lines, container, top=False)
        lines.append("    }")
        lines.append("")
        lines.append("    void shadow_flush_impl() {")
        self._emit_shadow_flush_body(lines, container, top=False)
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
        initializers.append("shadow(this)")

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

        self._emit_shadow_block(lines, top, top=True)

        for child in top.children:
            lines.append(self._child_member_decl(child, throw_expr="kThrowOnError"))
        lines.append("")

        lines.append("private:")
        lines.append("    void shadow_read_hw_impl() {")
        self._emit_shadow_read_body(lines, top, top=True)
        lines.append("    }")
        lines.append("")
        lines.append("    void shadow_flush_impl() {")
        self._emit_shadow_flush_body(lines, top, top=True)
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

    def _emit_shadow_block(self, lines: List[str], container: ContainerModel, top: bool) -> None:
        owner_type = self.design.top_class_name if top else container.class_name
        lines.append("    class ShadowOps {")
        lines.append("    public:")
        lines.append(f"        explicit ShadowOps({owner_type}* owner) : owner_(owner) {{}}")
        lines.append("        void read_hw() { owner_->shadow_read_hw_impl(); }")
        lines.append("        void flush() { owner_->shadow_flush_impl(); }")
        lines.append("    private:")
        lines.append(f"        {owner_type}* owner_;")
        lines.append("    };")
        lines.append("")
        lines.append("    ShadowOps shadow;")
        lines.append("")

    def _emit_shadow_read_body(self, lines: List[str], container: ContainerModel, top: bool) -> None:
        for child in container.children:
            if isinstance(child, ChildSingle):
                target = child.target
                if isinstance(target, RegisterModel):
                    lines.append(
                        f"        if ({child.cpp_name}.supports_shadow_write()) {child.cpp_name}.shadow.read_hw();"
                    )
                else:
                    lines.append(f"        {child.cpp_name}.shadow.read_hw();")
            else:
                if isinstance(child.element, RegisterModel):
                    lines.append(f"        for (std::size_t i = 0; i < {child.cpp_name}.size(); ++i) {{")
                    lines.append(f"            auto& elem = {child.cpp_name}[i];")
                    lines.append("            if (elem.supports_shadow_write()) elem.shadow.read_hw();")
                    lines.append("        }")
                else:
                    lines.append(f"        for (std::size_t i = 0; i < {child.cpp_name}.size(); ++i) {{")
                    lines.append(f"            auto& elem = {child.cpp_name}[i];")
                    lines.append("            elem.shadow.read_hw();")
                    lines.append("        }")

    def _emit_shadow_flush_body(self, lines: List[str], container: ContainerModel, top: bool) -> None:
        for child in container.children:
            if isinstance(child, ChildSingle):
                target = child.target
                if isinstance(target, RegisterModel):
                    lines.append(
                        f"        if ({child.cpp_name}.supports_shadow_write()) {child.cpp_name}.shadow.flush();"
                    )
                else:
                    lines.append(f"        {child.cpp_name}.shadow.flush();")
            else:
                if isinstance(child.element, RegisterModel):
                    lines.append(f"        for (std::size_t i = 0; i < {child.cpp_name}.size(); ++i) {{")
                    lines.append(f"            auto& elem = {child.cpp_name}[i];")
                    lines.append("            if (elem.supports_shadow_write()) elem.shadow.flush();")
                    lines.append("        }")
                else:
                    lines.append(f"        for (std::size_t i = 0; i < {child.cpp_name}.size(); ++i) {{")
                    lines.append(f"            auto& elem = {child.cpp_name}[i];")
                    lines.append("            elem.shadow.flush();")
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


def _to_class_case(name: str) -> str:
    parts = [p for p in re.split(r"[^0-9a-zA-Z]+", name) if p]
    if not parts:
        return "RegisterMap"
    return "".join(p[0].upper() + p[1:] for p in parts)


def _data_lit(value: int) -> str:
    return f"static_cast<data_t>(0x{value:X}ull)"


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


def _onread_cpp(onread: Optional[OnReadType]) -> str:
    if onread is None:
        return "none"
    mapping = {
        OnReadType.rclr: "rclr",
        OnReadType.rset: "rset",
        OnReadType.ruser: "ruser",
    }
    return mapping[onread]


def _c_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
