from typing import TYPE_CHECKING

from peakrdl.plugins.exporter import ExporterSubcommandPlugin

from .exporter import CppExporter

if TYPE_CHECKING:
    import argparse
    from systemrdl.node import AddrmapNode


class Exporter(ExporterSubcommandPlugin):
    short_desc = "Generate C++ register map header from SystemRDL"

    def add_exporter_arguments(self, arg_group: "argparse._ActionsContainer") -> None:
        arg_group.add_argument(
            "--namespace",
            default=None,
            help="C++ namespace for generated output. Defaults to top addrmap instance name.",
        )
        arg_group.add_argument(
            "--class-name",
            default=None,
            help="Top-level generated C++ class name. Defaults to PascalCase(top instance name).",
        )
        arg_group.add_argument(
            "--error-style",
            choices=["exceptions", "status"],
            default="exceptions",
            help="Error handling mode for generated API. [exceptions]",
        )
        arg_group.add_argument(
            "--no-write-range-check",
            action="store_true",
            help="Disable generated runtime range validation for write() and shadow.write() values.",
        )

    def do_export(self, top_node: "AddrmapNode", options: "argparse.Namespace") -> None:
        exporter = CppExporter()
        exporter.export(
            top_node,
            path=options.output,
            namespace=options.namespace,
            class_name=options.class_name,
            error_style=options.error_style,
            check_write_range=not options.no_write_range_check,
        )
