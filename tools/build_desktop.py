"""Build a native, self-contained unsigned Remit installer from an allowlisted tree.

Requires Git, uv, pnpm and platform build tools on the BUILD machine only.
Windows: Inno Setup is fetched with a pinned checksum. macOS: Xcode CLI tools.
The staging directory is intentionally new; use --reuse after a failed build.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import zipfile

REPO = Path(__file__).resolve().parents[1]
LOCK = json.loads((REPO / "tools/desktop-dependencies.json").read_text())
WINDOWS = sys.platform == "win32"
VERSION = "2.0.1"


def run(args, *, cwd=REPO, env=None):
    args = [str(arg) for arg in args]
    print("+", args[0], " ".join(args[1:4]), flush=True)
    # Windows shell wrappers (pnpm.cmd) need their resolved path with shell=False.
    args[0] = shutil.which(args[0]) or args[0]
    subprocess.run(args, cwd=cwd, env=env, check=True, timeout=1800)


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def download(key, cache):
    entry = LOCK[key]
    destination = cache / entry["url"].rsplit("/", 1)[1]
    cache.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or sha256(destination) != entry["sha256"]:
        temp = destination.with_suffix(destination.suffix + ".partial")
        run(["curl", "--fail", "--location", "--retry", "3", "--output", temp, entry["url"]])
        if sha256(temp) != entry["sha256"]:
            raise RuntimeError(f"Checksum mismatch: {key}")
        temp.replace(destination)
    return destination


def unpack(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                if not (destination / name).resolve().is_relative_to(destination.resolve()):
                    raise RuntimeError("Unsafe archive path")
            bundle.extractall(destination)
    else:
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter="data")


def source_files(stage):
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=REPO).decode().split("\0")
    for name in filter(None, names):
        if name.startswith(("backend/app/", "assets/")) or name in {
            "LICENSE", "NOTICE.md", "THIRD_PARTY_NOTICES.md", "backend/uv.lock",
            "backend/pyproject.toml", "tools/desktop_runtime.py", "tools/verify_portable_runtime.py",
        }:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, target)
    # Build scripts can be exercised before the initial commit, using explicit paths only.
    for name in ("desktop_runtime.py", "verify_portable_runtime.py"):
        (stage / "tools").mkdir(exist_ok=True)
        shutil.copy2(REPO / "tools" / name, stage / "tools" / name)
    shutil.copytree(REPO / "frontend/dist", stage / "frontend/dist", dirs_exist_ok=True)
    (stage / "VERSION").write_text(VERSION, encoding="utf-8")
    (stage / "BUILD-INFO.json").write_text(json.dumps({
        "version": VERSION,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "platform": sys.platform, "architecture": platform.machine(),
        "python": LOCK["python"], "publisher_signed": False,
    }, indent=2), encoding="utf-8")
    (stage / "backend/.env.dev").write_text(
        "DEBUG=false\nLOG_LEVEL=INFO\nCODE_EXECUTION_BACKEND=python\n"
        "MATLAB_FALLBACK_TO_PYTHON=true\nLATEX_ENGINE=xelatex\n", encoding="utf-8")


def runtime_python(stage, build):
    runtime = stage / "runtime/python"
    python = runtime / ("python.exe" if WINDOWS else "bin/python3")
    if not python.exists():
        env = os.environ.copy()
        env["UV_PYTHON_INSTALL_DIR"] = str(build / "managed-python")
        run(["uv", "python", "install", LOCK["python"]], env=env)
        base = Path(subprocess.check_output(
            [shutil.which("uv"), "python", "find", "--managed-python", LOCK["python"]], env=env,
            text=True).strip()).resolve()
        base_root = base.parent if WINDOWS else base.parent.parent
        shutil.copytree(base_root, runtime, symlinks=True)
    requirements = build / "desktop-requirements.txt"
    run(["uv", "export", "--locked", "--no-dev", "--no-emit-project", "--quiet", "--output-file", requirements],
        cwd=REPO / "backend")
    # This is an isolated COPY owned by the installer, never uv's managed source.
    run(["uv", "pip", "sync", "--python", python, "--break-system-packages", "--require-hashes", requirements])
    # Retain installed third-party metadata/license files and an inventory for audits.
    run(["uv", "pip", "list", "--python", python, "--format", "json"])
    inventory = subprocess.check_output([shutil.which("uv"), "pip", "list", "--python", str(python), "--format", "json"])
    (stage / "licenses").mkdir(exist_ok=True)
    (stage / "licenses/python-packages.json").write_bytes(inventory)
    shutil.copy2(requirements, stage / "licenses/python-requirements.txt")
    return python


def runtime_tex(stage, build):
    target = stage / "runtime/tinytex"
    if not target.exists():
        archive = download("tinytex_windows" if WINDOWS else "tinytex_macos", build / "downloads")
        expanded = build / "tex-extracted"
        unpack(archive, expanded)
        candidates = [p for p in expanded.iterdir() if (p / "bin").is_dir()]
        if len(candidates) != 1:
            raise RuntimeError("Unexpected TinyTeX archive layout")
        shutil.copytree(candidates[0], target, symlinks=True)
    binary = target / "bin" / ("windows" if WINDOWS else "universal-darwin")
    tlmgr = binary / ("tlmgr.bat" if WINDOWS else "tlmgr")
    env = os.environ.copy()
    env["PATH"] = str(binary) + os.pathsep + env.get("PATH", "")
    env["TEXLIVE_DOWNLOADER"] = "curl"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    # A fixed mirror keeps the database and signature from different redirects
    # from disagreeing while CTAN mirrors are synchronizing.
    run([tlmgr, "option", "repository", "https://ctan.math.illinois.edu/systems/texlive/tlnet"], env=env)
    run([tlmgr, "update", "--self"], env=env)
    run([tlmgr, "option", "docfiles", "1"], env=env)
    tlcommand = ([target / "tlpkg/tlperl/bin/perl.exe", target / "texmf-dist/scripts/texlive/tlmgr.pl"]
                 if WINDOWS else [tlmgr])
    if WINDOWS:
        env["PERL5LIB"] = str(target / "tlpkg/tlperl/lib")
    # Resolve document dependencies at build time, never in the user's first session.
    run([*tlcommand, "install", "ctex", "fandol", "xecjk", "xetex", "geometry", "amsmath",
         "amsfonts", "booktabs", "fancyhdr", "fontspec", "unicode-math", "enumitem",
         "titlesec", "titling", "setspace", "caption", "float", "multirow",
         "pgf", "listings", "tools", "hyperref", "xcolor", "natbib", "etoolbox"], env=env)
    run([*tlcommand, "install", "--reinstall", "fandol"], env=env)
    font = target / "texmf-dist/fonts/opentype/public/fandol/FandolHei-Regular.otf"
    if not font.is_file():
        raise RuntimeError("TinyTeX did not supply Fandol CJK font")
    (stage / "backend/fonts").mkdir(exist_ok=True)
    shutil.copy2(font, stage / "backend/fonts" / font.name)
    shutil.copytree(target / "texmf-dist/doc/fonts/fandol", stage / "licenses/fandol", dirs_exist_ok=True)
    packages = subprocess.check_output([str(p) for p in [*tlcommand, "info", "--only-installed"]], env=env)
    (stage / "licenses/tex-packages.txt").write_bytes(packages)


def mac_openmp(stage, build):
    if WINDOWS:
        return
    # XGBoost's wheel searches Homebrew for OpenMP. Bundle it and rewrite the
    # load command so end users never need Homebrew or a compiler.
    run(["brew", "install", "libomp"])
    prefix = Path(subprocess.check_output(["brew", "--prefix", "libomp"], text=True).strip())
    library = stage / "runtime/python/lib/python3.12/site-packages/xgboost/lib"
    shutil.copy2(prefix / "lib/libomp.dylib", library / "libomp.dylib")
    run(["install_name_tool", "-change", "@rpath/libomp.dylib", "@loader_path/libomp.dylib", library / "libxgboost.dylib"])
    for name in ("libomp.dylib", "libxgboost.dylib"):
        run(["codesign", "--force", "--sign", "-", library / name])
    shutil.copy2(download("llvm_license", build / "downloads"), stage / "licenses/LLVM-LICENSE.txt")
    (stage / "licenses/libomp-build.json").write_text(
        subprocess.check_output(["brew", "info", "--json=v2", "libomp"], text=True), encoding="utf-8")


def verify(stage, python, build):
    sys.path.insert(0, str(REPO / "tools"))
    from desktop_runtime import environment
    data = build / "verification data 中文"
    env = environment(data, stage)
    # Remove PATH access to the build host's Python, Node, Pandoc and TeX.
    system_path = str(Path(os.environ["SystemRoot"]) / "System32") if WINDOWS else "/usr/bin:/bin:/usr/sbin:/sbin"
    binary = stage / "runtime/tinytex/bin" / ("windows" if WINDOWS else "universal-darwin")
    env["PATH"] = os.pathsep.join([str(python.parent), str(binary), system_path])
    env["REMIT_DATA_DIR"] = str(data)
    reports = build / "reports"
    reports.mkdir(exist_ok=True)
    run([python, "-B", stage / "tools/verify_portable_runtime.py", "--install-root", stage,
         "--with-latex", "--report", reports / "runtime.json"], cwd=data, env=env)
    run([python, "-B", stage / "tools/desktop_runtime.py", "--smoke", "--report", reports / "services.json"],
        cwd=data, env=env)


def windows_installer(stage, build, output):
    redist = download("vc_redist", build / "downloads")
    (stage / "runtime/installers").mkdir(exist_ok=True)
    shutil.copy2(redist, stage / "runtime/installers/vc_redist.x64.exe")
    inno = build / "inno"
    compiler = inno / "ISCC.exe"
    if not compiler.exists():
        installer = download("inno", build / "downloads")
        run([installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/PORTABLE=1", f"/DIR={inno}"])
    language = download("inno_chinese", build / "downloads")
    shutil.copy2(language, inno / "Languages/ChineseSimplified.isl")
    run([compiler, f"/DStage={stage}", f"/DOutDir={output}", f"/DVersion={VERSION}",
         REPO / "tools/installer/desktop.iss"])


def mac_installer(stage, build, output):
    app = build / "dmg/Remit.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    # The full runtime is already built inside Resources; no absolute build paths in the launcher.
    launcher = build / "launcher.c"
    launcher.write_text(r'''#include <mach-o/dyld.h>
#include <unistd.h>
#include <limits.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
  char path[PATH_MAX], actual[PATH_MAX], python[PATH_MAX], script[PATH_MAX];
  uint32_t n=sizeof(path);
  if (_NSGetExecutablePath(path,&n) || !realpath(path,actual)) return 1;
  *strrchr(actual,'/')=0;
  snprintf(python,sizeof(python),"%s/../Resources/runtime/python/bin/python3",actual);
  snprintf(script,sizeof(script),"%s/../Resources/tools/desktop_runtime.py",actual);
  char **args=calloc(argc+4,sizeof(char*)); args[0]=python;args[1]="-B";args[2]=script;
  for(int i=1;i<argc;i++) args[i+2]=argv[i];
  execv(python,args); perror("Remit"); return 1;
}
''', encoding="utf-8")
    run(["cc", launcher, "-o", contents / "MacOS/Remit"])
    iconset = build / "Remit.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
            run(["sips", "-z", size * scale, size * scale, stage / "assets/remit-m-icon.png", "--out", iconset / name])
    run(["iconutil", "-c", "icns", iconset, "-o", stage / "Remit.icns"])
    with (contents / "Info.plist").open("wb") as dest:
        plistlib.dump({"CFBundleIdentifier": "org.remit.workbench", "CFBundleName": "Remit",
                      "CFBundleDisplayName": "Remit", "CFBundleExecutable": "Remit",
                      "CFBundleIconFile": "Remit.icns", "CFBundlePackageType": "APPL",
                      "CFBundleShortVersionString": "0.2.0", "CFBundleVersion": "4",
                      "LSMinimumSystemVersion": "15.0", "NSHighResolutionCapable": True,
                      "LSUIElement": True}, dest)
    # Ad-hoc signatures allow native execution on Apple silicon, but give no publisher identity.
    run(["codesign", "--force", "--deep", "--sign", "-", app])
    link = build / "dmg/Applications"
    if not link.exists():
        link.symlink_to("/Applications", target_is_directory=True)
    arch = "arm64" if platform.machine() == "arm64" else "x64"
    run(["hdiutil", "create", "-volname", "Remit", "-srcfolder", app.parent,
         "-ov", "-format", "UDZO", output / f"Remit-{VERSION}-macOS-{arch}.dmg"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", required=True, type=Path)
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--stage-only", action="store_true")
    args = parser.parse_args()
    if sys.platform not in {"win32", "darwin"}:
        parser.error("Build on native Windows or macOS")
    build = args.build_root.resolve()
    if WINDOWS and not str(build).isascii():
        parser.error("TinyTeX build tools require an ASCII build path, e.g. E:/RemitDesktopBuild")
    stage = build / ("stage/Remit" if WINDOWS else "dmg/Remit.app/Contents/Resources")
    if stage.exists() and not args.reuse:
        parser.error("Build exists; choose a fresh directory or --reuse. No files were deleted.")
    stage.mkdir(parents=True, exist_ok=True)
    output = build / "output"
    output.mkdir(exist_ok=True)
    if not args.skip_frontend:
        env = os.environ.copy()
        env.update(VITE_API_BASE_URL="", VITE_WS_URL="")
        run(["pnpm", "build"], cwd=REPO / "frontend", env=env)
    source_files(stage)
    python = runtime_python(stage, build)
    mac_openmp(stage, build)
    runtime_tex(stage, build)
    verify(stage, python, build)
    if not args.stage_only:
        (windows_installer if WINDOWS else mac_installer)(stage, build, output)
        for artifact in output.iterdir():
            if artifact.suffix in {".exe", ".dmg"}:
                artifact.with_suffix(artifact.suffix + ".sha256").write_text(
                    f"{sha256(artifact)}  {artifact.name}\n", encoding="ascii")
    print(f"Verified build: {output}", flush=True)


if __name__ == "__main__":
    main()
