"""Crash reports.

If Perch dies (a crash inside GTK, a fatal Python error), the next start says so and shows a report:
where it crashed, and the last steps Perch took. The steps name only kinds of things (a post page,
the compose box, the Like action), never posts, people, messages or links, and nothing leaves the
computer unless you send it yourself (Copy Report, or Report on GitHub, which opens a form you
submit).

The crash part is written by Python's faulthandler while the process dies; the steps are written
line by line as they happen, so both survive the crash.
"""

import faulthandler
import os
import signal
import sys
import threading
import time
import traceback
import urllib.parse
import warnings
from collections import deque
from pathlib import Path

from gi.repository import GLib

from perch import VERSION

ISSUES_URL = "https://github.com/eboye/perch/issues/new"
_TRAIL_LINES = 80
_URL_LIMIT = 7000  # GitHub (and browsers) cut longer links

_dir = Path(GLib.get_user_cache_dir()) / "perch" / "crash"
_trail: deque[str] = deque(maxlen=_TRAIL_LINES)
_trail_file = None
_trail_written = 0
_fault_file = None  # kept open: faulthandler writes to it as Perch dies


def install() -> str | None:
    """Start recording. Returns the report of the previous run if it crashed, else None."""
    global _trail_file, _fault_file
    try:
        _dir.mkdir(parents=True, exist_ok=True)
        # The last run crashed: keep its files until the report has been shown (see dismiss()).
        if (_dir / "fault.log").is_file() and (_dir / "fault.log").stat().st_size:
            (_dir / "fault.log").replace(_dir / "crashed-fault.log")
            if (_dir / "trail.log").is_file():
                (_dir / "trail.log").replace(_dir / "crashed-trail.log")
    except OSError as err:
        print(f"[perch] crash reports: {err}")
    previous = _previous_report()
    try:
        _fault_file = open(_dir / "fault.log", "w")  # noqa: SIM115 - must stay open
        _trail_file = open(_dir / "trail.log", "w", buffering=1)  # noqa: SIM115 - line-buffered, stays open
    except OSError as err:
        print(f"[perch] crash reports off: {err}")
        return previous
    try:
        faulthandler.enable(_fault_file, all_threads=True, c_stack=True)  # Python 3.14+: the C stack too
    except TypeError:
        faulthandler.enable(_fault_file, all_threads=True)
    # GLib's fatal errors (g_error) end with a breakpoint trap, which faulthandler doesn't watch.
    if hasattr(signal, "SIGTRAP"):
        try:
            faulthandler.register(signal.SIGTRAP, _fault_file, all_threads=True, chain=True)
        except (RuntimeError, ValueError):
            pass
    _hook_errors()
    note(f"Perch {VERSION} started")
    return previous


def note(step: str) -> None:
    """Record a step (kinds of things only: never names, text, ids or links)."""
    global _trail_written
    line = f"{time.strftime('%H:%M:%S')} {step}"
    _trail.append(line)
    if _trail_file is None:
        return
    try:
        _trail_written += 1
        if _trail_written > _TRAIL_LINES * 10:  # Perch runs for days: keep the file short
            _trail_file.seek(0)
            _trail_file.truncate()
            _trail_file.write("\n".join(_trail) + "\n")
            _trail_written = len(_trail)
        else:
            _trail_file.write(line + "\n")
    except (OSError, ValueError):
        pass


def dismiss() -> None:
    """The crash report was shown: don't offer it again."""
    for name in ("crashed-fault.log", "crashed-trail.log"):
        try:
            (_dir / name).unlink()
        except OSError:
            pass


def _hook_errors() -> None:
    """Python errors in callbacks and GLib warnings go in the steps (type and place, not the message)."""
    previous_excepthook = sys.excepthook

    def excepthook(kind, value, tb):
        note(f"error {kind.__name__} at {_where(tb)}")
        previous_excepthook(kind, value, tb)

    sys.excepthook = excepthook
    previous_thread_hook = threading.excepthook

    def thread_hook(args):
        note(f"error {args.exc_type.__name__} at {_where(args.exc_traceback)} (thread)")
        previous_thread_hook(args)

    threading.excepthook = thread_hook
    previous_showwarning = warnings.showwarning

    def showwarning(message, category, filename, lineno, file=None, line=None):
        text = str(message).splitlines()[0] if str(message) else ""
        # GLib warnings name the GLib source line and the problem; keep that, drop addresses.
        text = " ".join(word for word in text.split() if not word.strip("'").startswith("0x"))
        note(f"warning {category.__name__} at {Path(filename).name}:{lineno}: {text[:160]}")
        previous_showwarning(message, category, filename, lineno, file, line)

    warnings.showwarning = showwarning


def _where(tb) -> str:
    frames = traceback.extract_tb(tb)[-3:] if tb else []
    return " < ".join(f"{Path(frame.filename).name}:{frame.lineno} {frame.name}" for frame in reversed(frames)) or "?"


def _previous_report() -> str | None:
    """The report of a run that ended in a crash (faulthandler wrote something), until dismissed."""
    try:
        fault = (_dir / "crashed-fault.log").read_text(errors="replace").strip()
    except OSError:
        return None
    if not fault:
        return None
    try:
        trail = (_dir / "crashed-trail.log").read_text(errors="replace").strip().splitlines()[-_TRAIL_LINES:]
    except OSError:
        trail = []
    try:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime((_dir / "crashed-fault.log").stat().st_mtime))
    except OSError:
        when = "?"
    return "\n".join(
        [
            f"Perch crashed ({when}).",
            "",
            _versions(),
            "",
            "What Perch was doing (most recent last):",
            *trail,
            "",
            "Where it crashed:",
            _trim_fault(fault),
        ]
    )


def _trim_fault(fault: str) -> str:
    """faulthandler lists the crashing thread (and the C stack) first; keep the start of it, and
    write the home folder as ~ so the report doesn't show your user name."""
    home = str(Path.home())
    text = fault.replace(home, "~")
    # faulthandler lists the other threads first; the crashing one ("Current thread …") and its C
    # stack are what matter, so they go first (and survive the cut below).
    blocks = [block for block in text.split("\n\n") if block.strip()]
    current = [block for block in blocks if block.lstrip().startswith("Current thread")]
    first = [block for block in blocks if block.lstrip().startswith("Fatal Python error")]
    others = [block for block in blocks if block not in current and block not in first]
    lines = "\n\n".join(first + current + others).splitlines()
    return "\n".join(lines[:150])


def _versions() -> str:
    from gi.repository import Adw, Gtk

    parts = [
        f"Perch {VERSION}",
        f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}",
        f"libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}",
        f"GLib {GLib.MAJOR_VERSION}.{GLib.MINOR_VERSION}.{GLib.MICRO_VERSION}",
        f"Python {sys.version.split()[0]}",
    ]
    runtime = _flatpak_runtime()
    if runtime:
        parts.append(f"Flatpak {runtime}")
    if session := os.environ.get("XDG_SESSION_TYPE"):
        parts.append(session)
    return " · ".join(parts)


def _flatpak_runtime() -> str | None:
    try:
        for line in Path("/.flatpak-info").read_text().splitlines():
            if line.startswith("runtime="):
                return line.split("=", 1)[1].removeprefix("runtime/")
    except OSError:
        pass
    return None


def issue_url(report: str) -> str:
    """A new-issue form on GitHub with the report filled in (shortened to fit in a link)."""
    title = "Perch crashed"
    for line in report.splitlines():
        if line.startswith("Fatal Python error:"):
            title = f"Crash: {line.removeprefix('Fatal Python error:').strip()}"
            break
    lines = report.splitlines()
    marker = "… (shortened to fit in a link; Copy Report has all of it)"

    def build(body_lines):
        body = "**What were you doing when it happened?**\n\n\n**Report**\n```\n" + "\n".join(body_lines) + "\n```\n"
        return f"{ISSUES_URL}?{urllib.parse.urlencode({'title': title, 'body': body})}"

    # Too long for a link: drop the oldest steps first, then the end of the crash part.
    steps_at = lines.index("What Perch was doing (most recent last):") + 1 if "What Perch was doing (most recent last):" in lines else len(lines)
    crash_at = lines.index("Where it crashed:") if "Where it crashed:" in lines else len(lines)
    head, steps, tail = lines[:steps_at], lines[steps_at:crash_at], lines[crash_at:]
    url = build(lines)
    while len(url) > _URL_LIMIT and steps:
        steps = steps[5:]
        url = build([*head, marker, *steps, *tail])
    while len(url) > _URL_LIMIT and len(tail) > 5:
        tail = tail[:-5]
        url = build([*head, marker, *steps, *tail, marker])
    return url
