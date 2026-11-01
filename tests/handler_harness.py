"""Internal 1.x handler regression harness; never a public entrypoint or FCC proof.

New session/artifact/check gates are tested through the public SDK in test_driver_v2.
For these former query handlers this harness invokes the function directly so their
file/adapter algorithms remain covered without fabricating authorization or tokens.
Original write handlers keep their real private confirmation gate.
"""

import sys
import time

from xpedition_cli import main as entry
from xpedition_cli.cli.registry import _handler_module
from xpedition_cli.errors import CLIError
from xpedition_cli.output import emit, failure, success

parse_argv = entry.parse_argv
help_text = entry.help_text


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        command, options = entry.parse_argv(argv)
    except CLIError:
        return entry._handler_main(argv)
    if command is None or options.get("help") or not command.extra.get("generic_gate"):
        return entry._handler_main(argv)
    started = time.perf_counter()
    try:
        module, _, name = command.handler.partition(":")
        function = getattr(_handler_module(module), name)
        data = function(
            {
                **options,
                "_command": command.path,
                "_check_domain": command.extra.get("check_domain"),
            }
        )
        emit(
            success(data, started, options.get("fields")),
            options.get("format", "json"),
            bool(options.get("compact")),
        )
        return 0
    except CLIError as error:
        emit(failure(error, started), *entry._output_preferences(options, argv))
        return error.exit_code


_handler_main = main
