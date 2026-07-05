from __future__ import annotations

import pathlib
import sysconfig

_stdlib_email = pathlib.Path(sysconfig.get_paths()["stdlib"]) / "email"
_local_email = pathlib.Path(__file__).resolve().parent
__path__ = [str(_local_email), str(_stdlib_email)]

_stdlib_init = _stdlib_email / "__init__.py"
if _stdlib_init.exists():
    exec(compile(_stdlib_init.read_text(encoding="utf-8"), str(_stdlib_init), "exec"))
