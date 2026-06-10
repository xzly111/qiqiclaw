#!/usr/bin/env python3
"""
QiQi Claw CLI launcher (Python entry point).

Called by the qiqiclaw bash wrapper.
Subcommands: gateway, cron, doctor, etc.
"""

if __name__ == "__main__":
    from qiqiclaw_cli.main import main
    main()
