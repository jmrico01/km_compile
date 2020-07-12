# Standard build script for Kapricorn Media projects
# Must be run from the root directory

import argparse
from enum import Enum
import hashlib
import os
import platform
import queue
import random
import shutil
import subprocess
import sys
import threading

class Platform(Enum):
    WINDOWS = "Windows"
    LINUX = "Linux"
    MAC = "Darwin"

PLATFORM = ({ p.value: p for p in list(Platform) })[platform.system()]

class CompileMode(Enum):
    DEBUG    = "debug"
    INTERNAL = "internal"
    RELEASE  = "release"

class TargetType(Enum):
    EXECUTABLE = "exe"
    LIB_DYNAMIC = "lib_dynamic"
    LIB_STATIC = "lib_static"

class Define:
    def __init__(self, name, value=None):
        self.name = name
        self.value = value

    def to_compiler_flag(self):
        flag_str = "-D" + self.name
        if self.value is not None:
            flag_str += "=" + self.value

        return flag_str

class PlatformTargetOptions:
    def __init__(self, defines, compiler_flags, linker_flags):
        self.defines = defines
        self.compiler_flags = compiler_flags
        self.linker_flags = linker_flags

    def get_compiler_flags(self):
        return " ".join(
            [d.to_compiler_flag() for d in self.defines] +
            [flag for flag in self.compiler_flags]
        )

    def get_linker_flags(self):
        return " ".join([flag for flag in self.linker_flags])

class BuildTarget:
    def __init__(self, name, source_file, type, defines=[], platform_options={}):
        self.name = name
        self.source_file = source_file
        self.type = type
        self.defines = defines
        self.platform_options = platform_options

    def get_output_name(self):
        if self.type == TargetType.EXECUTABLE:
            if PLATFORM == Platform.WINDOWS:
                return self.name + "_win32.exe"
            elif PLATFORM == Platform.LINUX:
                return self.name + "_linux"
            elif PLATFORM == Platform.MAC:
                return self.name + "_macos"
        else:
            raise Exception("Unsupported target type: {}".format(self.type))

    def get_compiler_flags(self):
        compiler_flags = " ".join([d.to_compiler_flag() for d in self.defines])
        if PLATFORM in self.platform_options:
            compiler_flags = " ".join([
                compiler_flags,
                self.platform_options[PLATFORM].get_compiler_flags()
            ])

        return compiler_flags

    def get_linker_flags(self):
        linker_flags = ""
        if PLATFORM in self.platform_options:
            linker_flags = " ".join([
                linker_flags,
                self.platform_options[PLATFORM].get_linker_flags()
            ])

        return linker_flags

class CopyDir:
    def __init__(self, src, dst):
        self.src = src
        self.dst = dst

class LibExternal:
    def __init__(self, name, path, compiledNames = None, dllNames = None):
        self.name = name
        self.path = path
        self.compiledNames = compiledNames
        self.dllNames = dllNames

# Important directory & file paths
paths = {}
paths["root"] = os.getcwd()

includeDirs = {}

sys.path.insert(0, os.path.join(paths["root"], "compile"))
import app_info

def normalize_path_slashes(path):
    return path.replace("/", os.sep)

def fill_paths_and_include_dirs():
    paths["build"]          = paths["root"]  + "/build"
    paths["data"]           = paths["root"]  + "/data"
    paths["deploy"]         = paths["root"]  + "/deploy"
    paths["libs-external"]  = paths["root"]  + "/libs/external"
    paths["libs-internal"]  = paths["root"]  + "/libs/internal"
    paths["src"]            = paths["root"]  + "/src"

    paths["build-logs"]     = paths["build"] + "/logs"

    # Source hashes for if-changed compilation
    paths["src-hashes"]     = paths["build"] + "/src_hashes"
    paths["src-hashes-old"] = paths["build"] + "/src_hashes_old"

    # Other project-specific paths
    for name, path in app_info.PATHS.items():
        paths[name] = path

    for name in paths:
        paths[name] = normalize_path_slashes(paths[name])

    for lib in app_info.LIBS_EXTERNAL:
        libPath = paths["libs-external"] + "/" + lib.path
        includeDirs[lib.name] = libPath + "/include"

    for name in includeDirs:
        includeDirs[name] = normalize_path_slashes(includeDirs[name])

def remake_dest_and_copy_dir(src_path, dst_path):
    # Re-create (clear) the directory
    if os.path.exists(dst_path):
        shutil.rmtree(dst_path)
    os.makedirs(dst_path)

    # Copy
    for file_name in os.listdir(src_path):
        file_path = os.path.join(src_path, file_name)
        if os.path.isfile(file_path):
            shutil.copy2(file_path, dst_path)
        elif os.path.isdir(file_path):
            shutil.copytree(file_path, os.path.join(dst_path, file_name))

def make_and_clear_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

    for file_name in os.listdir(path):
        file_path = os.path.join(path, file_name)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print("Failed to clean {}: {}".format(file_path, str(e)))

def get_common_defines(compile_mode):
    defines = []
    if PLATFORM == Platform.WINDOWS:
        defines.append(Define("GAME_WIN32", "1"))
        defines.append(Define("_CRT_SECURE_NO_WARNINGS"))
    elif PLATFORM == Platform.LINUX:
        defines.append(Define("GAME_LINUX", "1"))
    elif PLATFORM == Platform.MAC:
        defines.append(Define("GAME_MACOS", "1"))

    if compile_mode == CompileMode.DEBUG:
        defines.append(Define("GAME_INTERNAL", "1"))
        defines.append(Define("GAME_SLOW",     "1"))
    elif compile_mode == CompileMode.INTERNAL:
        defines.append(Define("GAME_INTERNAL", "1"))
        defines.append(Define("GAME_SLOW",     "0"))
    elif compile_mode == CompileMode.RELEASE:
        defines.append(Define("GAME_INTERNAL", "0"))
        defines.append(Define("GAME_SLOW",     "0"))

    return defines

def win_compile(target, compile_mode):
    compiler_flags = ""

    # Add defines/macros
    compiler_flags = " ".join([
        compiler_flags
    ] + [d.to_compiler_flag() for d in get_common_defines(compile_mode)])

    # Add general compiler flags
    compiler_flags = " ".join([
        compiler_flags,
        "-nologo",        # disable the "Microsoft C/C++ Optimizing Compiler" message
        "-Gm-",           # disable incremental build things
        "-GR-",           # disable type information
        "-EHa-",          # disable exception handling
        "-EHsc",          # handle stdlib errors
        "-std:c++latest", # use latest C++ standard (aggregate initialization...)
        "-Z7"             # minimal "old school" debug information
    ])
    if compile_mode == CompileMode.DEBUG:
        compiler_flags = " ".join([
            compiler_flags,
            "-MTd", # static link of C runtime library (multithreaded debug version)
            "-Od",  # no optimization
            "-Oi",  # ...except for compiler intrinsics
        ])
    elif compile_mode == CompileMode.INTERNAL or compile_mode == CompileMode.RELEASE:
        compiler_flags = " ".join([
            compiler_flags,
            "-MT", # static link of C runtime library (multithreaded release version)
            "-Ox"  # full optimization
        ])

    # Add compiler warning flags
    compiler_flags = " ".join([
        compiler_flags,
        "-WX", # treat warnings as errors
        "-W4", # level 4 warnings
    ])
    if compile_mode == CompileMode.DEBUG:
        compiler_flags = " ".join([
            compiler_flags,
            "-wd4100", # unused function arguments
            "-wd4189", # local variable is initialized but not referenced
            "-wd4505", # unreferenced local function has been removed
            "-wd4702", # unreachable code (early return for debugging)
        ])

    # Add include paths
    compiler_flags = " ".join([
        compiler_flags,
        "-I\"" + paths["src"] + "\"",
        "-I\"" + paths["libs-internal"] + "\""
    ] + [ "-I\"" + path + "\"" for path in includeDirs.values() ])

    # Add all custom defines + compiler flags
    compiler_flags = " ".join([
        compiler_flags,
        target.get_compiler_flags()
    ])

    """
    # TODO hmm... is this a Hack
    if target.name == "nopasanada":
        compiler_warning_flags = " ".join([
            compiler_warning_flags,
            "/wd4267", # conversion from X to Y, possible loss of data
            "/wd4456", # declaration of X hides previous local declaration
        ])
    if compile_mode == CompileMode.DEBUG:
        compiler_warning_flags = " ".join([
            compiler_warning_flags,
            "/wd4189", # local variable is initialized but not referenced
            "/wd4702", # unreachable code (early return for debugging)
        ])
    """

    linker_flags = ""

    # Add general linker flags
    linker_flags = " ".join([
        "-incremental:no",  # disable incremental linking
        "-opt:ref"          # get rid of extraneous linkages
    ])

    # Add libraries
    linker_flags = " ".join([
        linker_flags,
        "kernel32.lib"
    ])

    indStr = ""
    if compile_mode == CompileMode.DEBUG:
        indStr = "debug"
    elif compile_mode == CompileMode.INTERNAL or compile_mode == CompileMode.RELEASE:
        indStr = "release"
    else:
        # TODO shouldn't have to check this everywhere
        raise Exception("Unknown compile mode {}".format(compile_mode))

    for lib in app_info.LIBS_EXTERNAL:
        if lib.compiledNames is not None:
            linker_flags += " -LIBPATH:\"" + os.path.join(paths["libs-external"], lib.path, "win32", indStr) + "\""
            linker_flags += " " + lib.compiledNames[indStr]

    # Add all custom linker flags
    linker_flags = " ".join([
        linker_flags,
        target.get_linker_flags()
    ])

    # Clear old PDB files
    # TODO with multiple compile targets, idk about this
    for file_name in os.listdir(paths["build"]):
        if ".pdb" in file_name:
            try:
                os.remove(os.path.join(paths["build"], file_name))
            except:
                print("Couldn't remove " + file_name)

    exe_name = target.get_output_name()
    map_name = target.name + "_win32.map"
    pdb_name = target.name + "_game" + str(random.randrange(99999)) + ".pdb"
    src_name = os.path.join(paths["root"], target.source_file)

    compile_command = " ".join([
        "cl", compiler_flags, "-Fe" + exe_name, "-Fm" + map_name, "\"" + src_name + "\"",
        "-link", linker_flags, "-PDB:" + pdb_name
    ])

    load_compiler = "call \"" + paths["win32-vcvarsall"] + "\" x64"

    subprocess.call(" & ".join([
        "pushd \"" + paths["build"] + "\"",
        load_compiler,
        compile_command,
        "popd"
    ]), shell=True)

    for lib in app_info.LIBS_EXTERNAL:
        if lib.dllNames is not None:
            dll_path_src = os.path.join(paths["libs-external"], lib.path, "win32", indStr, lib.dllNames[indStr])
            dll_path_dst = os.path.join(paths["build"], lib.dllNames[indStr])
            shutil.copyfile(dll_path_src, dll_path_dst)

    app_info.post_compile_custom(paths)

def win_run(target):
    os.system(" & ".join([
        "pushd " + paths["build"],
        target.name + "_win32.exe",
        "popd"
    ]))

def win_deploy(target):
    deploy_bundle_name = target.name
    deploy_bundle_path = os.path.join(paths["deploy"], deploy_bundle_name)
    remake_dest_and_copy_dir(paths["build"], deploy_bundle_path)
    for fileName in os.listdir(deploy_bundle_path):
        if fileName not in app_info.DEPLOY_FILES:
            filePath = os.path.join(deploy_bundle_path, fileName)
            if os.path.isfile(filePath):
                os.remove(filePath)
            elif os.path.isdir(filePath):
                shutil.rmtree(filePath)

    deployZipPath = os.path.join(paths["deploy"], "0. Unnamed")
    shutil.make_archive(deployZipPath, "zip", root_dir=paths["deploy"], base_dir=deploy_bundle_name)

def linux_compile(target, compile_mode):
    compiler_flags = ""

    # Add defines/macros
    compiler_flags = " ".join([
        compiler_flags
    ] + [d.to_compiler_flag() for d in get_common_defines(compile_mode)])

    # Add general compiler flags
    compiler_flags = " ".join([
        compiler_flags,
        "-std=c++17",     # use C++17 standard
        "-ggdb3",         # generate level 3 (max) GDB debug info.
        "-fno-rtti",      # disable run-time type info
        "-fno-exceptions" # disable C++ exceptions (ew)
    ])
    if compile_mode == CompileMode.DEBUG:
        compiler_flags = " ".join([
            compiler_flags,
            "-O0", # no optimization
        ])
    elif compile_mode == CompileMode.INTERNAL or compile_mode == CompileMode.RELEASE:
        compiler_flags = " ".join([
            compiler_flags,
            "-O3", # level 3 optimizations
        ])

    # Add compiler warning flags
    compiler_flags = " ".join([
        compiler_flags,
        "-Werror",  # treat warnings as errors
        "-Wall",    # enable all warnings

        "-Wno-char-subscripts", # using char as an array subscript
    ])
    if compile_mode == CompileMode.DEBUG:
        compiler_flags = " ".join([
            compiler_flags,
            "-Wno-unused-function"  # unused function
        ])

    # Add include paths
    compiler_flags = " ".join([
        compiler_flags,
        "-I'" + paths["src"] + "'",
        "-I'" + paths["libs-internal"] + "'"
    ] + [ "-I'" + path + "'" for path in includeDirs.values() ])

    # Add all custom defines + compiler flags
    compiler_flags = " ".join([
        compiler_flags,
        target.get_compiler_flags()
    ])

    linker_flags = ""

    # Add general linker flags
    linker_flags = " ".join([
        "-fvisibility=hidden"
    ])

    # Add libraries
    linker_flags = " ".join([
        linker_flags,
        "-lm",
        "-lpthread"
    ])

    # TODO compiled libs aren't added on linux

    # Add all custom linker flags
    linker_flags = " ".join([
        linker_flags,
        target.get_linker_flags()
    ])

    exe_name = target.get_output_name()
    src_name = os.path.join(paths["root"], target.source_file)

    compile_command = " ".join([
        "g++-9", compiler_flags, "'" + src_name + "'", "-o " + exe_name, linker_flags
    ])

    os.system("bash -c \"" + " ; ".join([
        "pushd '" + paths["build"] + "' > /dev/null",
        compile_command,
        "popd > /dev/null"
    ]) + "\"")

def linux_run():
    os.system(paths["build"] + os.sep + app_info.PROJECT_NAME + "_linux")

def mac_compile(compile_mode):
    raise Exception("bruh... gotta fix this before using it")

    macros = " ".join([
        "-DGAME_MACOS"
    ])
    if compile_mode == CompileMode.DEBUG:
        macros = " ".join([
            macros,
            "-DGAME_INTERNAL=1",
            "-DGAME_SLOW=1"
        ])
    elif compile_mode == CompileMode.INTERNAL:
        macros = " ".join([
            macros,
            "-DGAME_INTERNAL=1",
            "-DGAME_SLOW=0"
        ])
    elif compile_mode == CompileMode.RELEASE:
        macros = " ".join([
            macros,
            "-DGAME_INTERNAL=0",
            "-DGAME_SLOW=0"
        ])

    compilerFlags = " ".join([
        "-std=c++11",     # use C++11 standard
        "-lstdc++",       # link to C++ standard library
        "-fno-rtti",      # disable run-time type info
        "-fno-exceptions" # disable C++ exceptions (ew)
    ])
    if compile_mode == CompileMode.DEBUG:
        compilerFlags = " ".join([
            compilerFlags,
            "-g" # generate debug info
        ])
    elif compile_mode == CompileMode.INTERNAL or compile_mode == CompileMode.RELEASE:
        compilerFlags = " ".join([
            compilerFlags,
            "-O3" # full optimization
        ])

    compilerWarningFlags = " ".join([
        "-Werror",  # treat warnings as errors
        "-Wall",    # enable all warnings

        # disable the following warnings:
        "-Wno-missing-braces",  # braces around initialization of subobject (?)
        "-Wno-char-subscripts", # using char as an array subscript
        "-Wno-unused-function"
    ])

    includePaths = " ".join([
        "-I" + paths["include-freetype-mac"]
    ])

    frameworks = " ".join([
        "-framework Cocoa",
        "-framework OpenGL",
        "-framework AudioToolbox",
        "-framework CoreMIDI"
    ])
    linkerFlags = " ".join([
        #"-fvisibility=hidden"
    ])
    libPaths = " ".join([
        "-L" + paths["lib-freetype-mac"]
    ])
    libs = " ".join([
        "-lfreetype"
    ])

    compileLibCommand = " ".join([
        "clang",
        macros, compilerFlags, compilerWarningFlags, includePaths,
        "-dynamiclib", paths["main-cpp"],
        "-o " + app_info.PROJECT_NAME + "_game.dylib",
        linkerFlags, libPaths, libs
    ])

    compileCommand = " ".join([
        "clang", "-DGAME_PLATFORM_CODE",
        macros, compilerFlags, compilerWarningFlags, #includePaths,
        frameworks,
        paths["macos-main-mm"],
        "-o " + app_info.PROJECT_NAME + "_macos"
    ])

    os.system("bash -c \"" + " ; ".join([
        "pushd " + paths["build"] + " > /dev/null",
        compileLibCommand,
        compileCommand,
        "popd > /dev/null"
    ]) + "\"")

def mac_run():
    os.system(paths["build"] + os.sep + app_info.PROJECT_NAME + "_macos")

def calc_file_md5(filePath):
    md5 = hashlib.md5()
    with open(filePath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            md5.update(chunk)
    
    return md5.hexdigest()

def compute_src_hashes():
    with open(paths["src-hashes"], "w") as out:
        for root, _, files in os.walk(paths["src"]):
            for fileName in files:
                filePath = os.path.join(root, fileName)
                out.write(filePath + "\n")
                out.write(calc_file_md5(filePath) + "\n")

def did_files_change():
    hashPath = paths["src-hashes"]
    oldHashPath = paths["src-hashes-old"]

    if os.path.exists(hashPath):
        if os.path.exists(oldHashPath):
            os.remove(oldHashPath)
        os.rename(hashPath, oldHashPath)
    else:
        return True

    compute_src_hashes()
    if os.path.getsize(hashPath) != os.path.getsize(oldHashPath) \
    or open(hashPath, "r").read() != open(oldHashPath, "r").read():
        return True

    return False

def clean():
    make_and_clear_dir(paths["build"])
    make_and_clear_dir(paths["deploy"])

def run(target):
    if PLATFORM == Platform.WINDOWS:
        win_run(target)
    elif PLATFORM == Platform.LINUX:
        linux_run(target)
    elif PLATFORM == Platform.MAC:
        mac_run(target)
    else:
        raise Exception("Unsupported platform: " + PLATFORM)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", help="compilation mode")
    parser.add_argument("--ifchanged", action="store_true",
        help="run the specified compile command only if files have changed")
    parser.add_argument("--deploy", action="store_true",
        help="package and deploy a game build after compiling")
    args = parser.parse_args()

    fill_paths_and_include_dirs()

    if not os.path.exists(paths["build"]):
        os.makedirs(paths["build"])
    if not os.path.exists(paths["deploy"]):
        os.makedirs(paths["deploy"])

    if args.ifchanged:
        if not did_files_change():
            print("No changes, nothing to compile")
            return

    compile_mode_dict = { cm.value: cm for cm in list(CompileMode) }

    if args.mode == "clean":
        clean()
    elif args.mode == "run":
        run(app_info.TARGETS[0])
    elif args.mode in compile_mode_dict:
        compute_src_hashes()
        for copy_dir in app_info.COPY_DIRS:
            dir_src_path = os.path.join(paths["root"], copy_dir.src)
            dir_dst_path = os.path.join(paths["build"], copy_dir.dst)
            remake_dest_and_copy_dir(dir_src_path, dir_dst_path)
        if not os.path.exists(paths["build-logs"]):
            os.makedirs(paths["build-logs"])

        compile_mode = compile_mode_dict[args.mode]
        for target in app_info.TARGETS:
            if PLATFORM == Platform.WINDOWS:
                win_compile(target, compile_mode)
                if args.deploy:
                    win_deploy(target)
            elif PLATFORM == Platform.LINUX:
                linux_compile(target, compile_mode)
            elif PLATFORM == Platform.MAC:
                mac_compile(target, compile_mode)
            else:
                raise Exception("Unsupported platform: " + PLATFORM)
    else:
        raise Exception("Unrecognized argument: " + args.mode)

if __name__ == "__main__":
    main()
