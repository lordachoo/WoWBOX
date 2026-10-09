"""S3: dump WTF/Config.wtf (account names redacted) so the windowed-mode CVars can be confirmed.

    python s3_config_wtf.py --exe "C:/Games/WoW335/Wow.exe"
    python s3_config_wtf.py --wow-dir "C:/Games/WoW335"

Writes a sanitized copy to spikes/out/s3_config_sanitized.txt. Paste that file back.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from _common import banner, out_path

REDACT = re.compile(r'^(SET\s+(?:accountName|accountList)\s+)"[^"]*"', re.IGNORECASE)
INTERESTING = re.compile(
    r'^SET\s+(gx\w*|maxFPS\w*|useUiScale|uiScale|chatStyle|windowResizeLock|Sound_EnableSoundWhenGameIsInBG|'
    r'hwDetect|ffx\w*|realmList|locale)\s',
    re.IGNORECASE,
)


def wow_dir_from(args: argparse.Namespace) -> Path:
    if args.wow_dir:
        return Path(args.wow_dir)
    if args.exe:
        return Path(args.exe).parent
    raise SystemExit("pass --exe or --wow-dir")


def sanitize(lines: list[str]) -> list[str]:
    return [REDACT.sub(r'\1"<redacted>"', line) for line in lines]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe")
    ap.add_argument("--wow-dir")
    args = ap.parse_args()

    wow = wow_dir_from(args)
    cfg = wow / "WTF" / "Config.wtf"
    banner(f"S3 Config.wtf: {cfg}")
    if not cfg.exists():
        print("Config.wtf not found. Contents of the WoW folder / WTF folder:")
        for p in sorted(wow.glob("*")) + sorted((wow / "WTF").glob("*")):
            print("  ", p)
        raise SystemExit(1)

    lines = sanitize(cfg.read_text(encoding="utf-8", errors="replace").splitlines())
    print(f"{len(lines)} lines. Highlighted CVars:\n")
    for line in lines:
        if INTERESTING.match(line):
            print("  ", line)
    other_cfgs = sorted(p.name for p in (wow / "WTF").glob("*.wtf"))
    print(f"\n.wtf files in WTF/: {other_cfgs}")

    dest = out_path("s3_config_sanitized.txt")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nFull sanitized file written to {dest}\nPaste that file back (accountName/accountList are redacted).")


if __name__ == "__main__":
    main()
