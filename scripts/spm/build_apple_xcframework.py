#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import posixpath
import re
import shutil
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.spm import archive_builder
from scripts.spm import packaging
from scripts.spm import validate_mergeable_xcframework

_LOCAL_PUBLIC_HEADER_INCLUDE_PATTERN = re.compile(r'^(\s*#\s*(?:include|import)\s+)"([^"]+)"(.*)$')
_ANGLE_PUBLIC_HEADER_INCLUDE_PATTERN = re.compile(r'^(\s*#\s*(?:include|import)\s+)<([^>]+)>(.*)$')


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a mergeable Apple XCFramework for OpenFOAM-14.")
    parser.add_argument("--variant", required=True, choices=sorted(packaging.VARIANTS_BY_TARGET))
    parser.add_argument("--upstream-tag", required=True)
    parser.add_argument("--package-tag", help="Optional resolved package tag override for release metadata.")
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--developer-dir", help="Optional Xcode developer dir override.")
    parser.add_argument(
        "--platform",
        action="append",
        dest="platforms",
        default=[],
        help="Optional subset of SwiftPM platform identifiers to build (defaults to all variant platforms).",
    )
    parser.add_argument("--skip-smoke-test", action="store_true")
    return parser.parse_args()


def _run(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, check=True, cwd=cwd, env=env)


def _capture_output(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    process = subprocess.run(command, check=True, cwd=cwd, env=env, capture_output=True, text=True)
    return process.stdout.strip()


def _base_environment(arguments: argparse.Namespace) -> dict[str, str]:
    environment = dict(os.environ)
    if arguments.developer_dir:
        environment["DEVELOPER_DIR"] = arguments.developer_dir
    return environment


def _resolve_package_tag(arguments: argparse.Namespace) -> str:
    if arguments.package_tag:
        return arguments.package_tag
    return packaging.package_tag_for_upstream_tag(arguments.upstream_tag)


def _write_compiler_wrapper(wrapper_path: Path, ccache_path: str, compiler_path: str) -> None:
    wrapper_path.write_text(
        "#!/bin/sh\n"
        f'exec "{ccache_path}" "{compiler_path}" "$@"\n'
    )
    wrapper_path.chmod(0o755)


def _compiler_cache_environment(environment: dict[str, str], workspace_root: Path) -> dict[str, str]:
    ccache_path = os.environ.get("CCACHE_BIN") or shutil.which("ccache")
    if not ccache_path:
        return environment

    ccache_dir = environment.get("CCACHE_DIR")
    if ccache_dir:
        Path(ccache_dir).mkdir(parents=True, exist_ok=True)

    wrapper_root = workspace_root / ".compiler-wrappers"
    if wrapper_root.exists():
        shutil.rmtree(wrapper_root)
    wrapper_root.mkdir(parents=True)

    clang_path = _capture_output(["xcrun", "-f", "clang"], env=environment)
    clangxx_path = _capture_output(["xcrun", "-f", "clang++"], env=environment)

    clang_wrapper = wrapper_root / "clang"
    clangxx_wrapper = wrapper_root / "clang++"
    _write_compiler_wrapper(clang_wrapper, ccache_path, clang_path)
    _write_compiler_wrapper(clangxx_wrapper, ccache_path, clangxx_path)

    cached_environment = dict(environment)
    cached_environment["CC"] = str(clang_wrapper)
    cached_environment["CXX"] = str(clangxx_wrapper)
    cached_environment["OBJC"] = str(clang_wrapper)
    cached_environment["OBJCXX"] = str(clangxx_wrapper)
    cached_environment["LDPLUSPLUS"] = str(clangxx_wrapper)
    return cached_environment


def _parse_wmake_files(make_files_path: Path, base_dir: Path) -> list[Path]:
    text = make_files_path.read_text(encoding="utf-8", errors="ignore")
    variables: dict[str, str] = {}
    lines = text.splitlines()
    resolved_files: list[Path] = []
    skip_branch = False
    in_conditional = False

    index = 0
    while index < len(lines):
        raw_line = lines[index].strip()
        index += 1
        while raw_line.endswith("\\") and index < len(lines):
            raw_line = raw_line[:-1].strip() + " " + lines[index].strip()
            index += 1

        if not raw_line or raw_line.startswith(("#", "//", "/*", "*")):
            continue

        if raw_line.startswith("ifeq"):
            in_conditional = True
            skip_branch = "SunOS" in raw_line
            continue
        if raw_line.startswith("ifneq"):
            in_conditional = True
            skip_branch = False
            continue
        if raw_line == "else" and in_conditional:
            skip_branch = not skip_branch
            continue
        if raw_line == "endif" and in_conditional:
            in_conditional = False
            skip_branch = False
            continue
        if skip_branch:
            continue

        if "=" in raw_line and not raw_line.startswith("LIB"):
            key, value = raw_line.split("=", 1)
            variables[key.strip()] = value.strip()
            continue
        if raw_line.startswith("LIB"):
            continue

        expanded = raw_line
        for _ in range(5):
            changed = False
            for key, value in variables.items():
                for token in (f"$({key})", f"${{{key}}}"):
                    if token in expanded:
                        expanded = expanded.replace(token, value)
                        changed = True
            if not changed:
                break

        for item in expanded.split():
            if item.endswith(".Cver"):
                resolved_files.append(base_dir / f"{item[:-5]}.C")
            elif item.endswith((".C", ".c")):
                resolved_files.append(base_dir / item)
    return resolved_files


def _materialize_lninclude(target_dir: Path) -> Path:
    lninclude_dir = target_dir / "lnInclude"
    if lninclude_dir.exists():
        shutil.rmtree(lninclude_dir)
    lninclude_dir.mkdir(parents=True, exist_ok=True)

    for root, dirs, files in os.walk(target_dir):
        dirs[:] = [d for d in dirs if d not in {"lnInclude", "Make"}]
        root_path = Path(root)
        for filename in files:
            if filename.endswith((".H", ".h", ".C", ".I", ".ipp", ".txx")):
                source_file = root_path / filename
                destination_file = lninclude_dir / filename
                if not destination_file.exists() and not destination_file.is_symlink():
                    destination_file.symlink_to(source_file)
    return lninclude_dir


def _apply_darwin_source_patches(source_root: Path, upstream_tag: str) -> None:
    openfoam_dir = source_root / "src" / "OpenFOAM"
    posix_dir = source_root / "src" / "OSspecific" / "POSIX"
    pstream_dummy_dir = source_root / "src" / "Pstream" / "dummy"

    global_cver = openfoam_dir / "global" / "global.Cver"
    global_c = openfoam_dir / "global" / "global.C"
    if global_cver.is_file():
        rendered_global = (
            global_cver.read_text(encoding="utf-8")
            .replace("VERSION_STRING", "14")
            .replace("BUILD_STRING", f"14-{upstream_tag}")
        )
        global_c.write_text(rendered_global, encoding="utf-8")

    gzstream_version = openfoam_dir / "db" / "IOstreams" / "gzstream" / "version"
    if gzstream_version.is_file():
        gzstream_version.unlink()

    uint_h = openfoam_dir / "primitives" / "ints" / "uint" / "uint.H"
    if uint_h.is_file():
        uint_contents = uint_h.read_text(encoding="utf-8")
        if "APPLE_SPM_DARWIN_UINT_SHIM" not in uint_contents:
            shim = """
#ifdef __APPLE__
// APPLE_SPM_DARWIN_UINT_SHIM
#include "pTraits.H"
namespace Foam
{
MAXMIN(unsigned long, unsigned long, unsigned long)
MAXMIN(unsigned long, uint32_t, unsigned long)
MAXMIN(unsigned long, unsigned long, uint32_t)
MAXMIN(uint64_t, uint64_t, unsigned long)
MAXMIN(uint64_t, unsigned long, uint64_t)

inline word name(const unsigned long val)
{
    if constexpr (sizeof(unsigned long) == sizeof(uint64_t))
    {
        return name(static_cast<uint64_t>(val));
    }
    else
    {
        return name(static_cast<uint32_t>(val));
    }
}

inline word name(const uint16_t val)
{
    return name(static_cast<uint32_t>(val));
}

inline Istream& operator>>(Istream& is, unsigned long& i)
{
    if constexpr (sizeof(unsigned long) == sizeof(uint64_t))
    {
        uint64_t v = 0;
        is >> v;
        i = static_cast<unsigned long>(v);
        return is;
    }
    else
    {
        uint32_t v = 0;
        is >> v;
        i = static_cast<unsigned long>(v);
        return is;
    }
}

inline Ostream& operator<<(Ostream& os, const unsigned long i)
{
    if constexpr (sizeof(unsigned long) == sizeof(uint64_t))
    {
        return os << static_cast<uint64_t>(i);
    }
    else
    {
        return os << static_cast<uint32_t>(i);
    }
}

inline Istream& operator>>(Istream& is, uint16_t& i)
{
    uint32_t v = 0;
    is >> v;
    i = static_cast<uint16_t>(v);
    return is;
}

inline Ostream& operator<<(Ostream& os, const uint16_t i)
{
    return os << static_cast<uint32_t>(i);
}

template<>
class pTraits<uint16_t>
{
    uint16_t p_;

public:
    typedef uint16_t cmptType;
    static const direction dim = 3;
    static const direction rank = 0;
    static const direction nComponents = 1;

    static const char* const typeName;
    static const char* const componentNames[];
    static const uint16_t zero;
    static const uint16_t one;
    static const uint16_t min;
    static const uint16_t max;
    static const uint16_t rootMax;
    static const uint16_t rootMin;

    explicit pTraits(const uint16_t& p) noexcept : p_(p) {}
    explicit pTraits(Istream& is) { is >> p_; }
    operator uint16_t() const noexcept { return p_; }
    operator uint16_t&() noexcept { return p_; }
};
}
#endif
"""
            uint_contents = uint_contents.replace(
                "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n#endif",
                shim + "\n// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n#endif",
            )
            uint_h.write_text(uint_contents, encoding="utf-8")

    int_h = openfoam_dir / "primitives" / "ints" / "int" / "int.H"
    if int_h.is_file():
        int_contents = int_h.read_text(encoding="utf-8")
        if "APPLE_SPM_DARWIN_INT_SHIM" not in int_contents:
            shim = """
#ifdef __APPLE__
// APPLE_SPM_DARWIN_INT_SHIM
namespace Foam
{
MAXMIN(long, long, long)
MAXMIN(long, int32_t, long)
MAXMIN(long, long, int32_t)
MAXMIN(int64_t, int64_t, long)
MAXMIN(int64_t, long, int64_t)

inline word name(const long val)
{
    if constexpr (sizeof(long) == sizeof(int64_t))
    {
        return name(static_cast<int64_t>(val));
    }
    else
    {
        return name(static_cast<int32_t>(val));
    }
}

inline Istream& operator>>(Istream& is, long& i)
{
    if constexpr (sizeof(long) == sizeof(int64_t))
    {
        int64_t v = 0;
        is >> v;
        i = static_cast<long>(v);
        return is;
    }
    else
    {
        int32_t v = 0;
        is >> v;
        i = static_cast<long>(v);
        return is;
    }
}

inline Ostream& operator<<(Ostream& os, const long i)
{
    if constexpr (sizeof(long) == sizeof(int64_t))
    {
        return os << static_cast<int64_t>(i);
    }
    else
    {
        return os << static_cast<int32_t>(i);
    }
}
}
#endif
"""
            int_contents = int_contents.replace(
                "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n#endif",
                shim + "\n// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n#endif",
            )
            int_h.write_text(int_contents, encoding="utf-8")

    print_stack_c = posix_dir / "printStack.C"
    if print_stack_c.is_file():
        ps_contents = print_stack_c.read_text(encoding="utf-8")
        ps_contents = ps_contents.replace(
            "os << uintptr_t(addr);",
            "os << uint64_t(uintptr_t(addr));",
        )
        if "APPLE_SPM_POPEN_GUARD" not in ps_contents:
            ps_contents = ps_contents.replace(
                "FILE *cmdPipe = popen(cmd.c_str(), \"r\");",
                "// APPLE_SPM_POPEN_GUARD\n#if defined(__APPLE__) && defined(TARGET_OS_IPHONE) && TARGET_OS_IPHONE\n    FILE *cmdPipe = nullptr;\n    (void)cmd;\n#else\n    FILE *cmdPipe = popen(cmd.c_str(), \"r\");\n#endif",
            )
            ps_contents = ps_contents.replace(
                "pclose(cmdPipe);",
                "#if !(defined(__APPLE__) && defined(TARGET_OS_IPHONE) && TARGET_OS_IPHONE)\n        pclose(cmdPipe);\n#endif",
            )
        print_stack_c.write_text(ps_contents, encoding="utf-8")

    posix_c = posix_dir / "POSIX.C"
    if posix_c.is_file():
        posix_contents = posix_c.read_text(encoding="utf-8")
        if "APPLE_SPM_SYSTEM_GUARD" not in posix_contents:
            posix_contents = posix_contents.replace(
                "return ::system(command.c_str());",
                "// APPLE_SPM_SYSTEM_GUARD\n#if defined(__APPLE__) && defined(TARGET_OS_IPHONE) && TARGET_OS_IPHONE\n    (void)command;\n    return -1;\n#else\n    return ::system(command.c_str());\n#endif",
            )
            posix_c.write_text(posix_contents, encoding="utf-8")

    debug_c = openfoam_dir / "global" / "debug" / "debug.C"
    etc_config_dict = source_root / "etc" / "configDict"
    if debug_c.is_file() and etc_config_dict.is_file():
        debug_contents = debug_c.read_text(encoding="utf-8")
        if "APPLE_SPM_EMBEDDED_CONFIGDICT" not in debug_contents:
            embedded_config = etc_config_dict.read_text(encoding="utf-8")
            fallback_block = f"""// APPLE_SPM_EMBEDDED_CONFIGDICT
            fileNameList configDictFiles = findEtcFiles("configDict", false);
            configDictPtr_ = new dictionary();
            if (configDictFiles.empty())
            {{
                static const char* const kAppleEmbeddedConfigDict = R"FOAMCFG(
{embedded_config}
)FOAMCFG";
                IStringStream embeddedIs(kAppleEmbeddedConfigDict);
                configDictPtr_->merge(dictionary(embeddedIs));
            }}"""
            debug_contents = debug_contents.replace(
                '            fileNameList configDictFiles = findEtcFiles("configDict", true);\n            configDictPtr_ = new dictionary();',
                fallback_block,
                1,
            )
            debug_c.write_text(debug_contents, encoding="utf-8")

    _materialize_lninclude(openfoam_dir)
    _materialize_lninclude(posix_dir)
    _materialize_lninclude(pstream_dummy_dir)


def _write_openfoam_apple_compat_and_bridge(cmake_project_dir: Path) -> None:
    compat_header = cmake_project_dir / "apple_openfoam_compat.h"
    compat_header.write_text(
        """#ifndef APPLE_OPENFOAM_COMPAT_H
#define APPLE_OPENFOAM_COMPAT_H

#ifdef __APPLE__
#include <TargetConditionals.h>
#ifndef __LITTLE_ENDIAN
#define __LITTLE_ENDIAN 1234
#endif
#ifndef __BIG_ENDIAN
#define __BIG_ENDIAN 4321
#endif
#ifndef __BYTE_ORDER
#if defined(__BYTE_ORDER__) && (__BYTE_ORDER__ == __ORDER_BIG_ENDIAN__)
#define __BYTE_ORDER __BIG_ENDIAN
#else
#define __BYTE_ORDER __LITTLE_ENDIAN
#endif
#endif

#include <sys/types.h>
#include <sys/time.h>
#include <sys/stat.h>

#ifndef st_atim
#define st_atim st_atimespec
#endif
#ifndef st_mtim
#define st_mtim st_mtimespec
#endif
#ifndef st_ctim
#define st_ctim st_ctimespec
#endif

#ifdef __cplusplus
#include <cmath>
inline float j0f(float x) { return static_cast<float>(::j0(static_cast<double>(x))); }
inline float j1f(float x) { return static_cast<float>(::j1(static_cast<double>(x))); }
inline float y0f(float x) { return static_cast<float>(::y0(static_cast<double>(x))); }
inline float y1f(float x) { return static_cast<float>(::y1(static_cast<double>(x))); }
inline float jnf(int n, float x) { return static_cast<float>(::jn(n, static_cast<double>(x))); }
inline float ynf(int n, float x) { return static_cast<float>(::yn(n, static_cast<double>(x))); }

inline long double j0l(long double x) { return static_cast<long double>(::j0(static_cast<double>(x))); }
inline long double j1l(long double x) { return static_cast<long double>(::j1(static_cast<double>(x))); }
inline long double y0l(long double x) { return static_cast<long double>(::y0(static_cast<double>(x))); }
inline long double y1l(long double x) { return static_cast<long double>(::y1(static_cast<double>(x))); }
inline long double jnl(int n, long double x) { return static_cast<long double>(::jn(n, static_cast<double>(x))); }
inline long double ynl(int n, long double x) { return static_cast<long double>(::yn(n, static_cast<double>(x))); }
#endif
#endif

#endif
""",
        encoding="utf-8",
    )

    compat_include_dir = cmake_project_dir / "compat_include"
    (compat_include_dir / "sys").mkdir(parents=True, exist_ok=True)
    (compat_include_dir / "sys" / "sysmacros.h").write_text(
        """#ifndef APPLE_COMPAT_SYS_SYSMACROS_H
#define APPLE_COMPAT_SYS_SYSMACROS_H
#include <sys/types.h>
#endif
""",
        encoding="utf-8",
    )
    (compat_include_dir / "link.h").write_text(
        """#ifndef APPLE_COMPAT_LINK_H
#define APPLE_COMPAT_LINK_H
#include <mach-o/dyld.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
struct dl_phdr_info {
    uintptr_t dlpi_addr;
    const char *dlpi_name;
};
static inline int dl_iterate_phdr(
    int (*callback)(struct dl_phdr_info *info, size_t size, void *data),
    void *data
) {
    uint32_t count = _dyld_image_count();
    for (uint32_t i = 0; i < count; ++i) {
        struct dl_phdr_info info;
        info.dlpi_addr = (uintptr_t)_dyld_get_image_vmaddr_slide(i);
        info.dlpi_name = _dyld_get_image_name(i);
        int rc = callback(&info, sizeof(info), data);
        if (rc != 0) {
            return rc;
        }
    }
    return 0;
}
#ifdef __cplusplus
}
#endif
#endif
""",
        encoding="utf-8",
    )

    public_headers_dir = cmake_project_dir / "include" / "OpenFOAM"
    public_headers_dir.mkdir(parents=True, exist_ok=True)

    (public_headers_dir / "foamVersion.h").write_text(
        """#ifndef OPENFOAM_SPM_FOAM_VERSION_H
#define OPENFOAM_SPM_FOAM_VERSION_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

const char* foam_version_string(void);
const char* foam_build_string(void);
size_t foam_label_bytes(void);
size_t foam_scalar_bytes(void);

#ifdef __cplusplus
}
#endif

#endif
""",
        encoding="utf-8",
    )

    (public_headers_dir / "foamPrimitives.h").write_text(
        """#ifndef OPENFOAM_SPM_FOAM_PRIMITIVES_H
#define OPENFOAM_SPM_FOAM_PRIMITIVES_H

#ifdef __cplusplus
extern "C" {
#endif

typedef struct FoamVector3D {
    double x;
    double y;
    double z;
} FoamVector3D;

FoamVector3D foam_vector_make(double x, double y, double z);
double foam_vector_magnitude(FoamVector3D v);
double foam_vector_dot(FoamVector3D lhs, FoamVector3D rhs);
FoamVector3D foam_vector_cross(FoamVector3D lhs, FoamVector3D rhs);

#ifdef __cplusplus
}
#endif

#endif
""",
        encoding="utf-8",
    )

    (public_headers_dir / "foamBridge.h").write_text(
        """#ifndef OPENFOAM_SPM_FOAM_BRIDGE_H
#define OPENFOAM_SPM_FOAM_BRIDGE_H

#include <OpenFOAM/foamVersion.h>
#include <OpenFOAM/foamPrimitives.h>

#ifdef __cplusplus
extern "C" {
#endif

int foam_word_is_valid(const char* candidate);
int foam_dictionary_lookup_scalar(
    const char* dictionary_text,
    const char* keyword,
    double* out_value
);

#ifdef __cplusplus
}

#include <string>

namespace Foam {
namespace spm {

inline std::string version() {
    return std::string(foam_version_string());
}

inline std::string build() {
    return std::string(foam_build_string());
}

inline bool lookupScalar(
    const std::string& dictionaryText,
    const std::string& keyword,
    double& outValue
) {
    return foam_dictionary_lookup_scalar(
        dictionaryText.c_str(),
        keyword.c_str(),
        &outValue
    ) == 0;
}

}  // namespace spm
}  // namespace Foam
#endif

#endif
""",
        encoding="utf-8",
    )

    (public_headers_dir / "OpenFOAM.h").write_text(
        """#ifndef OPENFOAM_SPM_UMBRELLA_H
#define OPENFOAM_SPM_UMBRELLA_H

#include <OpenFOAM/foamVersion.h>
#include <OpenFOAM/foamPrimitives.h>
#include <OpenFOAM/foamBridge.h>

#endif
""",
        encoding="utf-8",
    )

    (cmake_project_dir / "foam_spm_bridge.C").write_text(
        """#include "include/OpenFOAM/OpenFOAM.h"

#include "foamVersion.H"
#include "label.H"
#include "scalar.H"
#include "vector.H"
#include "word.H"
#include "dictionary.H"
#include "IStringStream.H"

namespace Foam
{
const char* const pTraits<uint16_t>::typeName = "uint16_t";
const char* const pTraits<uint16_t>::componentNames[] = {""};
const uint16_t pTraits<uint16_t>::zero = 0;
const uint16_t pTraits<uint16_t>::one = 1;
const uint16_t pTraits<uint16_t>::min = 0;
const uint16_t pTraits<uint16_t>::max = 65535;
const uint16_t pTraits<uint16_t>::rootMax = 255;
const uint16_t pTraits<uint16_t>::rootMin = 0;
}

extern "C" {

const char* foam_version_string(void)
{
    return Foam::FOAMversion;
}

const char* foam_build_string(void)
{
    return Foam::FOAMbuild;
}

size_t foam_label_bytes(void)
{
    return sizeof(Foam::label);
}

size_t foam_scalar_bytes(void)
{
    return sizeof(Foam::scalar);
}

FoamVector3D foam_vector_make(double x, double y, double z)
{
    FoamVector3D result = {x, y, z};
    return result;
}

double foam_vector_magnitude(FoamVector3D v)
{
    const Foam::vector fv(v.x, v.y, v.z);
    return static_cast<double>(Foam::mag(fv));
}

double foam_vector_dot(FoamVector3D lhs, FoamVector3D rhs)
{
    const Foam::vector a(lhs.x, lhs.y, lhs.z);
    const Foam::vector b(rhs.x, rhs.y, rhs.z);
    return static_cast<double>(a & b);
}

FoamVector3D foam_vector_cross(FoamVector3D lhs, FoamVector3D rhs)
{
    const Foam::vector a(lhs.x, lhs.y, lhs.z);
    const Foam::vector b(rhs.x, rhs.y, rhs.z);
    const Foam::vector c = a ^ b;
    FoamVector3D result = {
        static_cast<double>(c.x()),
        static_cast<double>(c.y()),
        static_cast<double>(c.z())
    };
    return result;
}

int foam_word_is_valid(const char* candidate)
{
    if (candidate == nullptr || *candidate == '\\0')
    {
        return 0;
    }
    const std::string text(candidate);
    for (char ch : text)
    {
        if (!Foam::word::valid(ch))
        {
            return 0;
        }
    }
    return 1;
}

int foam_dictionary_lookup_scalar(
    const char* dictionary_text,
    const char* keyword,
    double* out_value
)
{
    if (dictionary_text == nullptr || keyword == nullptr || out_value == nullptr)
    {
        return -1;
    }
    try
    {
        Foam::IStringStream input(dictionary_text);
        Foam::dictionary dict(input);
        const Foam::scalar value = dict.lookup<Foam::scalar>(Foam::word(keyword));
        *out_value = static_cast<double>(value);
        return 0;
    }
    catch (...)
    {
        return -2;
    }
}

}
""",
        encoding="utf-8",
    )


def _prepare_cmake_source_root(source_root: Path, workspace_root: Path, upstream_tag: str) -> Path:
    openfoam_make_files = source_root / "src" / "OpenFOAM" / "Make" / "files"
    if not openfoam_make_files.is_file():
        return source_root

    _apply_darwin_source_patches(source_root, upstream_tag)

    cmake_project_dir = workspace_root / "cmake_src"
    if cmake_project_dir.exists():
        shutil.rmtree(cmake_project_dir)
    cmake_project_dir.mkdir(parents=True, exist_ok=True)

    _write_openfoam_apple_compat_and_bridge(cmake_project_dir)

    openfoam_dir = source_root / "src" / "OpenFOAM"
    posix_dir = source_root / "src" / "OSspecific" / "POSIX"
    pstream_dummy_dir = source_root / "src" / "Pstream" / "dummy"

    sources = (
        _parse_wmake_files(posix_dir / "Make" / "files", posix_dir)
        + _parse_wmake_files(pstream_dummy_dir / "Make" / "files", pstream_dummy_dir)
        + _parse_wmake_files(openfoam_dir / "Make" / "files", openfoam_dir)
        + [cmake_project_dir / "foam_spm_bridge.C"]
    )
    cmake_sources_block = "\n".join(f'    "{path.as_posix()}"' for path in sources)
    compat_header_path = (cmake_project_dir / "apple_openfoam_compat.h").as_posix()
    compat_include_path = (cmake_project_dir / "compat_include").as_posix()
    public_include_path = (cmake_project_dir / "include").as_posix()

    (cmake_project_dir / "CMakeLists.txt").write_text(
        f"""cmake_minimum_required(VERSION 3.22)
project(OpenFOAM LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(OpenFOAM SHARED
{cmake_sources_block}
)

set_target_properties(OpenFOAM PROPERTIES
    OUTPUT_NAME "OpenFOAM"
    MACOSX_RPATH ON
)

target_compile_definitions(OpenFOAM PRIVATE
    WM_DP
    WM_LABEL_SIZE=32
    NoRepository
    FOAM_USE_STAT
)

target_compile_options(OpenFOAM PRIVATE
    -include "{compat_header_path}"
    -Wno-unqualified-std-cast-call
    -Wno-deprecated-declarations
    -Wno-undefined-var-template
    -Wno-nonportable-include-path
)

target_include_directories(OpenFOAM PRIVATE
    "{compat_include_path}"
    "{public_include_path}"
    "{(openfoam_dir / 'lnInclude').as_posix()}"
    "{openfoam_dir.as_posix()}"
    "{(posix_dir / 'lnInclude').as_posix()}"
    "{posix_dir.as_posix()}"
    "{(pstream_dummy_dir / 'lnInclude').as_posix()}"
    "{pstream_dummy_dir.as_posix()}"
)

target_link_libraries(OpenFOAM PRIVATE z c++)

install(TARGETS OpenFOAM
    LIBRARY DESTINATION lib
    ARCHIVE DESTINATION lib
    RUNTIME DESTINATION bin
)
install(DIRECTORY "{(cmake_project_dir / 'include' / 'OpenFOAM').as_posix()}"
    DESTINATION include
)
""",
        encoding="utf-8",
    )
    return cmake_project_dir


def _cmake_configure_command(
    variant: packaging.Variant,
    platform: packaging.Platform,
    source_root: Path,
    build_dir: Path,
    install_dir: Path,
) -> list[str]:
    _ = variant
    return [
        "cmake",
        "-S",
        str(source_root),
        "-B",
        str(build_dir),
        "-G",
        "Xcode",
        f"-DCMAKE_TOOLCHAIN_FILE={packaging.TOOLCHAIN_FILE}",
        f"-DCMAKE_INSTALL_PREFIX={install_dir}",
        f"-DPLATFORM={platform.cmake_platform}",
        f"-DARCHS={';'.join(platform.archs)}",
        f"-DDEPLOYMENT_TARGET={platform.deployment_target}",
        "-DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED=NO",
        "-DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_REQUIRED=NO",
        "-DCMAKE_XCODE_ATTRIBUTE_CODE_SIGN_STYLE=Manual",
    ]


def _build_command(build_dir: Path) -> list[str]:
    return [
        "cmake",
        "--build",
        str(build_dir),
        "--config",
        "Release",
        "--target",
        "install",
        "--",
        "CODE_SIGNING_ALLOWED=NO",
        "CODE_SIGNING_REQUIRED=NO",
        "CODE_SIGN_STYLE=Manual",
    ]


def _archive_command(build_dir: Path, archive_path: Path, derived_data_path: Path, platform: packaging.Platform) -> list[str]:
    return [
        "xcodebuild",
        "archive",
        "-project",
        str(build_dir / "OpenFOAM.xcodeproj"),
        "-scheme",
        "OpenFOAM",
        "-configuration",
        "Release",
        "-destination",
        platform.xcode_destination,
        "-archivePath",
        str(archive_path),
        "-derivedDataPath",
        str(derived_data_path),
        f"ARCHS={' '.join(platform.archs)}",
        "SKIP_INSTALL=NO",
        "CODE_SIGNING_ALLOWED=NO",
        "CODE_SIGNING_REQUIRED=NO",
        "CODE_SIGN_STYLE=Manual",
        "MERGEABLE_LIBRARY=YES",
        "ONLY_ACTIVE_ARCH=NO",
    ]


def _stage_headers(
    install_dir: Path,
    output_dir: Path,
    module_name: str,
) -> Path:
    headers_source = install_dir / "include" / "OpenFOAM"
    if not headers_source.exists():
        raise FileNotFoundError(f"public headers not found at {headers_source}")

    headers_output = output_dir / "Headers"
    if headers_output.exists():
        shutil.rmtree(headers_output)
    shutil.copytree(headers_source, headers_output)
    (headers_output / "module.modulemap").write_text(
        f"""module {module_name} {{
    umbrella "."
    export *
    module * {{ export * }}
}}
"""
    )
    _rewrite_same_framework_header_includes(headers_output, module_name)
    return headers_output


def _normalize_public_header_include(current_header: Path, headers_root: Path, include_path: str) -> str | None:
    current_parent = current_header.parent.relative_to(headers_root).as_posix()
    normalized = posixpath.normpath(posixpath.join(current_parent, include_path))
    if normalized.startswith("../") or normalized == "..":
        return None
    resolved_path = headers_root / normalized
    if not resolved_path.is_file():
        return None
    return normalized


def _normalize_framework_style_include(current_header: Path, headers_root: Path, include_path: str) -> str | None:
    candidate_path = include_path.split("/", 1)[1] if "/" in include_path else include_path
    return _normalize_public_header_include(current_header, headers_root, candidate_path)


def _rewrite_same_framework_header_includes(headers_root: Path, framework_name: str) -> None:
    for header_path in sorted(headers_root.rglob("*.h")) + sorted(headers_root.rglob("*.H")):
        rewritten_lines: list[str] = []
        changed = False
        for line in header_path.read_text().splitlines(keepends=True):
            stripped_line = line.rstrip("\r\n")
            line_ending = line[len(stripped_line) :]
            local_match = _LOCAL_PUBLIC_HEADER_INCLUDE_PATTERN.match(stripped_line)
            if local_match:
                normalized_include = _normalize_public_header_include(header_path, headers_root, local_match.group(2))
                if normalized_include is not None:
                    rewritten_lines.append(
                        f'{local_match.group(1)}<{framework_name}/{normalized_include}>{local_match.group(3)}{line_ending}'
                    )
                    changed = True
                    continue

            angle_match = _ANGLE_PUBLIC_HEADER_INCLUDE_PATTERN.match(stripped_line)
            if not angle_match:
                rewritten_lines.append(line)
                continue

            normalized_include = _normalize_framework_style_include(header_path, headers_root, angle_match.group(2))
            if normalized_include is None:
                rewritten_lines.append(line)
                continue

            rewritten_lines.append(
                f'{angle_match.group(1)}<{framework_name}/{normalized_include}>{angle_match.group(3)}{line_ending}'
            )
            changed = True

        if changed:
            header_path.write_text("".join(rewritten_lines))


def _copy_framework_headers(headers_source: Path, destination_dir: Path) -> None:
    destination_dir.mkdir(parents=True, exist_ok=True)
    for source_path in headers_source.iterdir():
        if source_path.name == "module.modulemap":
            continue
        if source_path.is_dir():
            shutil.copytree(source_path, destination_dir / source_path.name)
            continue
        if source_path.is_file():
            shutil.copy2(source_path, destination_dir / source_path.name)


def _write_framework_module_map(module_map_path: Path, module_name: str) -> None:
    module_map_path.write_text(
        f"""framework module {module_name} {{
    umbrella "../Headers"
    export *
    module * {{ export * }}
}}
"""
    )


def _write_framework_info_plist(info_plist_path: Path, bundle_name: str) -> None:
    bundle_identifier = f"io.spmforge.{bundle_name.replace('_', '-')}"
    payload = {
        "CFBundleExecutable": bundle_name,
        "CFBundleIdentifier": bundle_identifier,
        "CFBundleName": bundle_name,
        "CFBundlePackageType": "FMWK",
        "CFBundleShortVersionString": "14.0",
        "CFBundleVersion": "14",
    }
    info_plist_path.write_bytes(plistlib.dumps(payload))


def _should_rewrite_install_name(platform: packaging.Platform) -> bool:
    return "arm64_32" not in platform.archs


def _stage_framework_bundle(
    source_binary: Path,
    headers_source: Path,
    output_dir: Path,
    bundle_name: str,
    module_name: str,
    platform: packaging.Platform,
    environment: dict[str, str],
) -> Path:
    framework_root = output_dir / f"{bundle_name}.framework"
    if framework_root.exists():
        shutil.rmtree(framework_root)

    if platform.swiftpm_platform == "macos":
        active_root = framework_root / "Versions" / "A"
        binary_path = active_root / bundle_name
        headers_path = active_root / "Headers"
        modules_path = active_root / "Modules"
        info_plist_path = active_root / "Resources" / "Info.plist"
        framework_root.mkdir(parents=True, exist_ok=True)
        headers_path.mkdir(parents=True, exist_ok=True)
        modules_path.mkdir(parents=True, exist_ok=True)
        info_plist_path.parent.mkdir(parents=True, exist_ok=True)
        install_name = f"@rpath/{bundle_name}.framework/Versions/A/{bundle_name}"
    else:
        binary_path = framework_root / bundle_name
        headers_path = framework_root / "Headers"
        modules_path = framework_root / "Modules"
        info_plist_path = framework_root / "Info.plist"
        headers_path.mkdir(parents=True, exist_ok=True)
        modules_path.mkdir(parents=True, exist_ok=True)
        install_name = f"@rpath/{bundle_name}.framework/{bundle_name}"

    shutil.copy2(source_binary, binary_path)
    _copy_framework_headers(headers_source, headers_path)
    _write_framework_module_map(modules_path / "module.modulemap", module_name)
    _write_framework_info_plist(info_plist_path, bundle_name)

    if platform.swiftpm_platform == "macos":
        versions_dir = framework_root / "Versions"
        (versions_dir / "Current").symlink_to("A")
        (framework_root / bundle_name).symlink_to(Path("Versions") / "Current" / bundle_name)
        (framework_root / "Headers").symlink_to(Path("Versions") / "Current" / "Headers")
        (framework_root / "Modules").symlink_to(Path("Versions") / "Current" / "Modules")
        (framework_root / "Resources").symlink_to(Path("Versions") / "Current" / "Resources")

    if _should_rewrite_install_name(platform):
        _run(["install_name_tool", "-id", install_name, str(binary_path)], env=environment)
    return framework_root


def _create_xcframework(
    variant: packaging.Variant,
    staged_frameworks: list[Path],
    output_dir: Path,
    environment: dict[str, str],
) -> Path:
    xcframework_path = output_dir / f"{variant.target_name}.xcframework"
    if xcframework_path.exists():
        shutil.rmtree(xcframework_path)

    command = ["xcodebuild", "-create-xcframework"]
    for staged_framework in staged_frameworks:
        command.extend(["-framework", str(staged_framework)])
    command.extend(["-output", str(xcframework_path)])
    _run(command, env=environment)
    return xcframework_path


def _zip_xcframework(xcframework_path: Path, zip_path: Path, environment: dict[str, str]) -> None:
    if zip_path.exists():
        zip_path.unlink()
    _run(
        [
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(xcframework_path),
            str(zip_path),
        ],
        env=environment,
    )


def _compute_checksum(zip_path: Path, environment: dict[str, str]) -> str:
    return _capture_output(["swift", "package", "compute-checksum", str(zip_path)], cwd=zip_path.parent, env=environment)


def _write_release_metadata(
    output_path: Path,
    variant: packaging.Variant,
    upstream_tag: str,
    package_tag: str,
    checksum: str,
    zip_path: Path,
) -> None:
    payload = packaging.build_artifact_metadata_payload(
        packaging.ReleaseAsset(
            variant=variant,
            upstream_tag=upstream_tag,
            package_tag=package_tag,
            checksum=checksum,
        ),
        artifact_path=str(zip_path),
    )
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _validate_xcframework(
    xcframework_path: Path,
    variant: packaging.Variant,
    required_platforms: list[str] | None = None,
) -> None:
    effective_platforms = required_platforms or [platform.swiftpm_platform for platform in variant.platforms]
    result = validate_mergeable_xcframework.validate_xcframework(
        xcframework_path,
        effective_platforms,
        require_dependencies=packaging.required_dependencies_for_variant(variant),
        require_strong_dependencies=packaging.required_strong_dependencies_for_variant(variant),
        require_weak_dependencies=packaging.required_weak_dependencies_for_variant(variant),
        forbid_dependencies=packaging.forbidden_dependencies_for_variant(variant),
    )
    if result["issues"]:
        print(json.dumps({"xcframeworks": [result]}, indent=2), file=sys.stderr)
        raise RuntimeError(f"xcframework validation failed for {xcframework_path}")


def _build_platform_slice(
    cmake_source_root: Path,
    variant: packaging.Variant,
    platform: packaging.Platform,
    workspace_root: Path,
    environment: dict[str, str],
) -> tuple[Path, Path]:
    build_dir = workspace_root / "build" / platform.swiftpm_platform
    install_dir = build_dir / "install"
    archive_path = build_dir / f"{variant.target_name}-{platform.swiftpm_platform}.xcarchive"
    derived_data_path = build_dir / "DerivedData"

    _run(
        _cmake_configure_command(
            variant,
            platform,
            cmake_source_root,
            build_dir,
            install_dir,
        ),
        env=environment,
    )
    _run(_build_command(build_dir), env=environment)
    _run(_archive_command(build_dir, archive_path, derived_data_path, platform), env=environment)

    dynamic_library = archive_builder.find_dynamic_library(
        archive_root=archive_path,
        derived_data_root=derived_data_path,
        library_basename="OpenFOAM",
    )
    return install_dir, dynamic_library


def _selected_platforms(variant: packaging.Variant, requested: list[str]) -> tuple[packaging.Platform, ...]:
    if not requested:
        return variant.platforms
    by_name = {platform.swiftpm_platform: platform for platform in variant.platforms}
    selected: list[packaging.Platform] = []
    for name in requested:
        if name not in by_name:
            raise ValueError(f"unsupported platform '{name}' for variant {variant.target_name}")
        selected.append(by_name[name])
    return tuple(selected)


def main() -> int:
    arguments = _parse_arguments()
    environment = _base_environment(arguments)
    source_root = arguments.source_root.resolve()
    variant = packaging.variant_for_target_name(arguments.variant)
    package_tag = _resolve_package_tag(arguments)
    platforms_to_build = _selected_platforms(variant, arguments.platforms)

    workspace_root = arguments.output_dir.resolve() / variant.target_name
    if workspace_root.exists():
        shutil.rmtree(workspace_root)
    workspace_root.mkdir(parents=True)
    environment = _compiler_cache_environment(environment, workspace_root)

    cmake_source_root = _prepare_cmake_source_root(source_root, workspace_root, arguments.upstream_tag)

    staged_frameworks = []
    headers_path: Path | None = None
    staging_root = workspace_root / "staging"

    for platform in platforms_to_build:
        install_dir, dynamic_library = _build_platform_slice(
            cmake_source_root,
            variant,
            platform,
            workspace_root,
            environment,
        )
        if headers_path is None:
            headers_path = _stage_headers(
                install_dir,
                staging_root / variant.target_name,
                variant.module_name,
            )
        staged_framework = _stage_framework_bundle(
            source_binary=dynamic_library,
            headers_source=headers_path,
            output_dir=staging_root / variant.target_name / platform.swiftpm_platform,
            bundle_name=variant.target_name,
            module_name=variant.module_name,
            platform=platform,
            environment=environment,
        )
        staged_frameworks.append(staged_framework)

    assert headers_path is not None

    xcframework_path = _create_xcframework(
        variant=variant,
        staged_frameworks=staged_frameworks,
        output_dir=workspace_root,
        environment=environment,
    )
    _validate_xcframework(
        xcframework_path,
        variant,
        required_platforms=[platform.swiftpm_platform for platform in platforms_to_build],
    )

    if not arguments.skip_smoke_test and any(p.swiftpm_platform == "macos" for p in platforms_to_build):
        _run(
            [
                sys.executable,
                str(packaging.SCRIPTS_ROOT / "smoke_test_package.py"),
                "--variant",
                variant.target_name,
                "--xcframework",
                str(xcframework_path),
            ],
            env=environment,
        )

    zip_path = workspace_root / packaging.asset_name_for_variant(variant, arguments.upstream_tag)
    _zip_xcframework(xcframework_path, zip_path, environment)
    checksum = _compute_checksum(zip_path, environment)

    release_metadata_path = workspace_root / f"{variant.target_name}.release.json"
    _write_release_metadata(release_metadata_path, variant, arguments.upstream_tag, package_tag, checksum, zip_path)

    print(
        json.dumps(
            {
                "target_name": variant.target_name,
                "package_tag": package_tag,
                "upstream_tag": arguments.upstream_tag,
                "artifact_path": str(zip_path),
                "metadata_path": str(release_metadata_path),
                "checksum": checksum,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
