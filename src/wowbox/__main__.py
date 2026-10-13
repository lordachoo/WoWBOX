"""Entry point: python -m wowbox [--config path]"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import shutil
import sys
from pathlib import Path


def app_dir() -> Path:
    """Folder that holds config/ and logs/: next to wowbox.exe when frozen, else the project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def setup_logging(log_dir: Path, level: str) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fh = logging.handlers.RotatingFileHandler(log_dir / "wowbox.log", maxBytes=5_000_000, backupCount=5,
                                              encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setLevel(getattr(logging, level.upper(), logging.INFO))
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
        root.addHandler(sh)


def main(argv: list[str] | None = None) -> int:
    base = app_dir()
    ap = argparse.ArgumentParser(prog="wowbox", description="Multibox controller for WotLK 3.3.5a")
    ap.add_argument("--config", type=Path, default=base / "config" / "team.yaml")
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--check-config", action="store_true", help="validate the config and exit")
    ap.add_argument("--no-attach", action="store_true", help="don't grab running WoW windows at startup")
    ap.add_argument("--quit-after", type=float, help=argparse.SUPPRESS)  # smoke tests
    args = ap.parse_args(argv)

    # A short GIL switch interval keeps the keyboard hook responsive (measured: p99 1.6 ms vs ~900 ms under load).
    sys.setswitchinterval(0.0005)
    setup_logging(base / "logs", args.log_level)
    log = logging.getLogger("wowbox")
    from .diagnostics import Diagnostics
    diag = Diagnostics(base / "logs")
    diag.install()  # crash.log, uncaught errors, and "did the last WoWBox shut down cleanly?"

    cfg_path: Path = args.config
    if not cfg_path.exists():
        example = base / "config" / "team.example.yaml"
        if not example.exists():
            print(f"config not found: {cfg_path}", file=sys.stderr)
            return 2
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(example, cfg_path)
        log.info("created %s from team.example.yaml - edit it (client.exe, team slots)", cfg_path)

    from .config import load_config
    try:
        cfg = load_config(cfg_path)
    except Exception as e:
        print(f"Invalid config {cfg_path}:\n{e}", file=sys.stderr)
        if args.check_config or sys.stderr is not None:
            return 2
        raise
    if args.check_config:
        print(f"{cfg_path}: OK ({len(cfg.team.slots)} slots, {len(cfg.routes)} routes, "
              f"{len(cfg.macros)} macros, {len(cfg.automation)} automation rules)")
        return 0

    from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication

    from .app import Controller
    from .platform import create_capture, create_platform
    from .ui.main_window import MainWindow
    from .ui.tray import Tray

    qt_log = logging.getLogger("qt")
    levels = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
              QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}
    qInstallMessageHandler(lambda kind, _ctx, msg: qt_log.log(levels.get(kind, logging.WARNING), "%s", msg))
    app = QApplication(sys.argv)
    app.setApplicationName("WoWBox")
    app.setQuitOnLastWindowClosed(False)
    windows, input_api = create_platform()
    controller = Controller(cfg_path.resolve(), cfg, windows, input_api, create_capture, diag=diag)
    window = MainWindow(controller)
    controller.window = window
    tray = Tray(controller, window)
    controller.sig_changed.connect(tray.refresh)
    tray.show()
    window.show()
    controller.start(attach=not args.no_attach)
    if args.quit_after:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(int(args.quit_after * 1000), window.quit_app)
    log.info("pid %d, config %s", os.getpid(), cfg_path)
    code = app.exec()
    diag.clean_exit()
    log.info("WoWBox exited (code %d)", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
