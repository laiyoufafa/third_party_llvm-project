#!/usr/bin/env python
# Copyright (C) 2021 Huawei Device Co., Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# 2021.3.15 build for OHOS LLVM.
#     Copyright (c) 2021 Huawei Device Co., Ltd. All rights reserved.

import os
import platform
import re
import datetime
import logging
import glob
import subprocess
import shutil
import argparse
import mingw


class BuildConfig():
    # Defines public methods and functions and obtains script parameters.

    def __init__(self):
        args = self.parse_args()
        self.do_build = not args.skip_build
        self.do_package = not args.skip_package
        self.build_name = args.build_name
        self.debug = args.debug
        self.no_lto = args.no_lto
        self.build_instrumented = args.build_instrumented
        self.xunit_xml_output = args.xunit_xml_output
        self.enable_assertions = args.enable_assertions
        self.need_libs = self.do_build and 'libs' not in args.no_build
        self.need_lldb_mi = self.do_build and 'lldb-mi' not in args.no_build
        self.need_lldb_server = self.do_build and 'lldb-server' not in args.no_build

        self.no_build_arm = args.skip_build or args.no_build_arm
        self.no_build_aarch64 = args.skip_build or args.no_build_aarch64
        self.no_build_x86_64 = args.skip_build or args.no_build_x86_64

        self.CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
        self.REPOROOT_DIR = os.path.abspath(os.path.join(self.CURRENT_DIR, '../../'))
        self.LLVM_PROJECT_DIR = os.path.abspath(os.path.join(self.REPOROOT_DIR, 'toolchain', 'llvm-project'))
        self.OUT_PATH = os.path.join(self.REPOROOT_DIR, 'out')
        self.TARGETS = 'AArch64;ARM;BPF;X86'
        self.ORIG_ENV = dict(os.environ)
        self.VERSION = None # autodetected

        self.OPENHOS_SFX = '-linux-ohos'
        self.LITEOS_SFX = '-liteos-ohos'
        self.LLDB_PY_VERSION = '3.10'
        self.CLANG_VERSION = '10.0.1'
        logging.basicConfig(level=logging.INFO)

    @staticmethod
    def parse_add_argument(parser):
        
        parser.add_argument(
            '--enable-assertions',
            action='store_true',
            default=False,
            help='Apply assertions, some parameters are affected.')

        parser.add_argument(
            '--build-name',
            default='dev',
            help='Release name for the package.')

        parser.add_argument(
            '--debug',
            action='store_true',
            default=False,
            help='Building Clang and LLVM Tools for Debugging (only affects stage2)')

        parser.add_argument(
            '--no-build-arm',
            action='store_true',
            default=False,
            help='Omit build os target: arm.')

        parser.add_argument(
            '--no-build-aarch64',
            action='store_true',
            default=False,
            help='Omit build os target: aarch64.')

        parser.add_argument(
            '--no-build-x86_64',
            action='store_true',
            default=False,
            help='Omit build os target: x86_64.')

        parser.add_argument(
            '--no-lto',
            action='store_true',
            default=False, 
            help='Accelerate builds by disabling LTO (only affects llvm product)')

        parser.add_argument(
            '--build-instrumented',
            action='store_true',
            default=False,
            help='Using the PGO instrumentation to build LLVM tool')

        parser.add_argument(
            '--xunit-xml-output',
            default=None,
            help='Output path for LLVM unit tests XML report')

    def parse_args(self):

        parser = argparse.ArgumentParser(description='Process some integers.')

        # Options to skip build or packaging, can't skip two
        build_package_group = parser.add_mutually_exclusive_group()
        build_package_group.add_argument(
            '--skip-build',
            '-sb',
            action='store_true',
            default=False,
            help='Omit the build, perform the packaging step directly.')

        build_package_group.add_argument(
            '--skip-package',
            '-sp',
            action='store_true',
            default=False,
            help='Omit the packaging, perform the packaging step directly.')

        self.parse_add_argument(parser)
       
        known_platforms = ('windows', 'libs', 'lldb-mi', 'lldb-server', 'linux', 'check-api')
        known_platforms_str = ', '.join(known_platforms)

        class SeparatedListByCommaAction(argparse.Action):
            def __call__(self, parser, namespace, vals, option_string):
                for val in vals.split(','):
                    if val in known_platforms:
                        continue
                    else:
                        error = '\'{}\' invalid.  Choose from {}'.format(val, known_platforms)
                        raise argparse.ArgumentError(self, error)
                setattr(namespace, self.dest, vals.split(','))

        parser.add_argument(
            '--no-build',
            action=SeparatedListByCommaAction,
            default=list(),
            help='Don\'t build toolchain for specified platforms.  Choices: ' + known_platforms_str)

        return parser.parse_args()


class ClangVersion(object):
    """Parse and save clang version from version file."""

    def __init__(self, version_file):
        self._parse_version_file(version_file)

    @staticmethod
    def _parse(text, key):
        return re.findall(r'%s\s+(\d+)' % key, text)[0]

    def _parse_version_file(self, version_file):
        with open(version_file, 'r') as fp:
            text = fp.read()
        self.major = self._parse(text, 'CLANG_VERSION_MAJOR')
        self.minor = self._parse(text, 'CLANG_VERSION_MINOR')
        self.patch = self._parse(text, 'CLANG_VERSION_PATCHLEVEL')

    def long_version(self):
        return '.'.join([self.major, self.minor, self.patch])

    def short_version(self):
        return '.'.join([self.major, self.minor])

    def major_version(self):
        return self.major


class BuildUtils(object):

    def __init__(self, build_config):
        self.build_config = build_config

        self.CMAKE_BIN_DIR = os.path.abspath(
            os.path.join(self.build_config.REPOROOT_DIR, 'prebuilts/cmake', self.platform_prefix(), 'bin'))

    def open_ohos_triple(self, arch):
        return arch + self.build_config.OPENHOS_SFX

    def liteos_triple(self, arch):
        return arch + self.build_config.LITEOS_SFX

    def set_clang_version(self, install_dir):
        self.build_config.VERSION = self.get_clang_version(install_dir).long_version()

    def invoke_ninja(self,
                     out_path,
                     env,
                     target=None,
                     install=True,
                     build_threads=False):

        ninja_bin_path = os.path.join(self.CMAKE_BIN_DIR, 'ninja')

        ninja_list = ['-l{}'.format(build_threads)] if build_threads else []

        ninja_target = [target] if target else []

        self.check_call([ninja_bin_path] + ninja_list + ninja_target, cwd=out_path, env=env)
        if install:
            self.check_call([ninja_bin_path, 'install'], cwd=out_path, env=env)

    def invoke_cmake(self,
                     cmake_path,
                     out_path,
                     invoke_defines,
                     env):

        cmake_bin_path = os.path.join(self.CMAKE_BIN_DIR, 'cmake')
        flags = ['-G', 'Ninja']
        flags += ['-DCMAKE_PREFIX_PATH=%s' % self.CMAKE_BIN_DIR]

        for key in invoke_defines:
            newdef = ''.join(['-D', key, '=', invoke_defines[key]])
            flags += [newdef]

        flags += [cmake_path]
        self.check_create_dir(out_path)

        self.check_call([cmake_bin_path] + flags, cwd=out_path, env=env)

    @staticmethod
    def logger():
        """Returns the module level logger."""
        return logging.getLogger(__name__)

    @staticmethod
    def get_clang_version(llvm_install):
        version_file = os.path.join(llvm_install, 'include', 'clang', 'Basic',
                                'Version.inc')
        return ClangVersion(version_file)

    def check_create_dir(self, path):
        if not os.path.exists(path):
            """Proxy for os.makedirs with logging and dry-run support."""
            self.logger().info('makedirs %s', path)
            os.makedirs(path)

    def check_rm_tree(self, tree_dir):
        """Removes directory tree."""
        def chmod_and_retry(func, path, _):
            if not os.access(path, os.W_OK):
                os.chmod(path, stat.S_IWUSR)
                return func(path)
            raise IOError("rmtree on %s failed" % path)

        if os.path.exists(tree_dir):
            self.logger().info('shutil rmtree %s', tree_dir)
            shutil.rmtree(tree_dir, onerror=chmod_and_retry)

    def check_copy_tree(self, src_dir, dst_dir):
        self.check_rm_tree(dst_dir)
        """Proxy for shutil.copytree with logging and dry-run support."""
        self.logger().info('copytree %s %s', src_dir, dst_dir)
        shutil.copytree(src_dir, dst_dir, symlinks=True)

    def check_copy_file(self, src_file, dst_file):
        if os.path.exists(src_file):
            """Proxy for shutil.copy2 with logging and dry-run support."""
            self.logger().info('copy %s %s', src_file, dst_file)
            shutil.copy2(src_file, dst_file)

    def check_call(self, cmd, *args, **kwargs):
        """subprocess.check_call with logging."""
        self.logger().info('check_call:%s %s',
                           datetime.datetime.now().strftime("%H:%M:%S"), subprocess.list2cmdline(cmd))

        subprocess.check_call(cmd, *args, **kwargs)

    def merge_out_path(self, *args):
        return os.path.abspath(os.path.join(self.build_config.OUT_PATH, *args))

    @staticmethod
    def use_platform():
        sysstr = platform.system().lower()
        arch = platform.machine()
        return "%s-%s" % (sysstr, arch)

    def platform_prefix(self):
        prefix = self.use_platform()
        if (prefix.endswith('x86_64')):
            return prefix[:-3]
        return prefix

    def host_is_linux(self):
        return self.use_platform().startswith('linux-')

    def host_is_darwin(self):
        return self.use_platform().startswith('darwin-')

    def rm_cmake_cache(self, cache_dir):
        for dirpath, dirs, files in os.walk(cache_dir):
            if 'CMakeCache.txt' in files:
                self.logger().info('rm CMakeCache.txt on %s', cache_dir)
                os.remove(os.path.join(dirpath, 'CMakeCache.txt'))
            if 'CMakeFiles' in dirs:
                self.logger().info('rm CMakeFiles on %s', cache_dir)
                self.check_rm_tree(os.path.join(dirpath, 'CMakeFiles'))

    @staticmethod
    def find_program(name):
        # FIXME: Do we need Windows support here?
        return os.popen('which ' + name).read().strip()

    # Base cmake options such as build type that are common across all invocations
    def base_cmake_defines(self):
        mac_min_version = '10.9'
        defines = {}

        defines['CMAKE_BUILD_TYPE'] = 'Release'
        defines['LLVM_ENABLE_ASSERTIONS'] = 'OFF'
        defines['LLVM_ENABLE_TERMINFO'] = 'OFF'
        defines['LLVM_ENABLE_THREADS'] = 'ON'
        defines['LLVM_USE_NEWPM'] = 'ON'
        defines['LLVM_ENABLE_BINDINGS'] = 'OFF'
        defines['CLANG_REPOSITORY_STRING'] = 'llvm-project'

        if self.host_is_darwin():
            defines['CMAKE_OSX_DEPLOYMENT_TARGET'] = mac_min_version
            defines['LLDB_INCLUDE_TESTS'] = 'OFF'
            defines['LIBCXX_INCLUDE_TESTS'] = 'OFF'

        defines['COMPILER_RT_BUILD_XRAY'] = 'OFF'
        return defines


class LlvmCore(BuildUtils):

    def __init__(self, build_config):
        super(LlvmCore, self).__init__(build_config)

    def build_llvm(self,
                   targets,
                   build_dir,
                   install_dir,
                   build_name,
                   extra_defines=None,
                   extra_env=None):

        common_defines = self.base_cmake_defines()
        common_defines['CMAKE_INSTALL_PREFIX'] = install_dir
        common_defines['LLVM_INSTALL_UTILS'] = 'ON'
        common_defines['LLVM_TARGETS_TO_BUILD'] = targets
        common_defines['LLVM_BUILD_LLVM_DYLIB'] = 'ON'

        build_number = ''
        if re.match(r'\d+-.\D*$', build_name):
            build_number, build_name = build_name.split('-', 1)
        elif re.match(r'^\d+$', build_name):
            build_number = build_name
            build_name = ''
        elif re.match(r'^\D+$', build_name):
            build_name = build_name
        else:
            raise Exception('Warning! Build name is invalid, because it must not contain digits. '
                            'If you want to pass digit version, please, use follow template: {NUMBER}-{SUFFIX} '
                            'or just pass number. Otherwise unit tests will fail with assertions')

        common_defines['CLANG_VENDOR'] = 'OHOS (%s) ' % build_name
        common_defines['CLANG_VENDOR_BUILD_VERSION'] = build_number
        common_defines.update(extra_defines)

        env = dict(self.build_config.ORIG_ENV)
        if extra_env is not None:
            env.update(extra_env)

        llvm_project_path = os.path.abspath(os.path.join(self.build_config.LLVM_PROJECT_DIR, 'llvm'))

        self.invoke_cmake(llvm_project_path,
                          build_dir,
                          common_defines,
                          env=env)

        self.invoke_ninja(out_path=build_dir,
                          env=env,
                          target=None,
                          install=True)


    def llvm_compile_darwin_defines(self, llvm_defines):
        if self.host_is_darwin():
            llvm_defines['LLDB_ENABLE_LIBEDIT'] = 'OFF'
            llvm_defines['LLDB_NO_DEBUGSERVER'] = 'ON'
            llvm_defines['LLDB_ENABLE_PYTHON'] = 'ON'
            llvm_defines['COMPILER_RT_BUILD_LIBFUZZER'] = 'OFF'
            llvm_defines['LLVM_BUILD_EXTERNAL_COMPILER_RT'] = 'ON'

    def llvm_compile_linux_defines(self, 
                                   llvm_defines, 
                                   debug_build=False,
                                   no_lto=False,
                                   build_instrumented=False):
        if self.host_is_linux():
            llvm_defines['LLVM_ENABLE_LLD'] = 'ON'
            llvm_defines['COMPILER_RT_BUILD_LIBFUZZER'] = 'ON'
            llvm_defines['LIBCXX_ENABLE_STATIC_ABI_LIBRARY'] = 'ON'
            llvm_defines['LIBCXX_ENABLE_ABI_LINKER_SCRIPT'] = 'OFF'
            llvm_defines['EXECUTION_ENGINE_USE_LLVM_UNWINDER'] = '1'
            llvm_defines['COMPILER_RT_USE_BUILTINS_LIBRARY'] = 'ON'
            llvm_defines['LIBUNWIND_USE_COMPILER_RT'] = 'ON'
            llvm_defines['LIBCXXABI_USE_COMPILER_RT'] = 'ON'
            llvm_defines['LIBCXX_USE_COMPILER_RT'] = 'ON'
            llvm_defines['LIBCXXABI_ENABLE_STATIC_UNWINDER'] = 'ON'
            llvm_defines['LIBCXXABI_USE_LLVM_UNWINDER'] = 'YES'
            llvm_defines['LIBCXXABI_STATICALLY_LINK_UNWINDER_IN_STATIC_LIBRARY'] = 'YES'
            llvm_defines['LLVM_BINUTILS_INCDIR'] = '/usr/include'
            

            if not build_instrumented and not no_lto and not debug_build:
                llvm_defines['LLVM_ENABLE_LTO'] = 'Thin'

    @staticmethod
    def llvm_compile_llvm_defines(llvm_defines, llvm_cc, llvm_cxx, cflags, ldflags):
        llvm_defines['LLVM_BUILD_RUNTIME'] = 'ON'
        llvm_defines['LLVM_ENABLE_PROJECTS'] = \
            'clang;lld;libunwind;libcxxabi;libcxx;compiler-rt;clang-tools-extra;openmp;lldb'
        llvm_defines['LLVM_ENABLE_BINDINGS'] = 'OFF'
        llvm_defines['CMAKE_C_COMPILER'] = llvm_cc
        llvm_defines['CMAKE_CXX_COMPILER'] = llvm_cxx
        llvm_defines['LLVM_ENABLE_LIBCXX'] = 'ON'
        llvm_defines['SANITIZER_ALLOW_CXXABI'] = 'OFF'
        llvm_defines['LIBOMP_ENABLE_SHARED'] = 'FALSE'
        llvm_defines['OPENMP_TEST_FLAGS'] = '-Wl,-ldl'
        llvm_defines['CLANG_BUILD_EXAMPLES'] = 'OFF'
        llvm_defines['LLDB_ENABLE_LIBEDIT'] = 'OFF'
        llvm_defines['LLDB_ENABLE_PYTHON'] = 'ON'
        llvm_defines['COMPILER_RT_BUILD_SANITIZERS'] = 'OFF'
        llvm_defines['COMPILER_RT_BUILD_MEMPROF'] = 'OFF'
        llvm_defines['CMAKE_ASM_FLAGS'] = cflags
        llvm_defines['CMAKE_C_FLAGS'] = cflags
        llvm_defines['CMAKE_CXX_FLAGS'] = '%s -stdlib=libc++' % cflags
        llvm_defines['CMAKE_EXE_LINKER_FLAGS'] = ldflags
        llvm_defines['CMAKE_SHARED_LINKER_FLAGS'] = ldflags
        llvm_defines['CMAKE_MODULE_LINKER_FLAGS'] = ldflags

    def llvm_compile(self,
                     build_name,
                     out_dir,
                     debug_build=False,
                     no_lto=False,
                     build_instrumented=False,
                     xunit_xml_output=None):

        llvm_clang_install = os.path.abspath(os.path.join(self.build_config.REPOROOT_DIR,
                                                          'prebuilts/clang/ohos', self.use_platform(),
                                                          'clang-%s' % self.build_config.CLANG_VERSION))
        llvm_path = self.merge_out_path('llvm_make')
        llvm_cc = os.path.join(llvm_clang_install, 'bin', 'clang')
        llvm_cxx = os.path.join(llvm_clang_install, 'bin', 'clang++')
        llvm_profdata = os.path.join(llvm_clang_install, 'bin', 'llvm-profdata')

        if self.host_is_darwin():
            ldflags = ''
        else:
            ldflags = '-fuse-ld=lld'
        ldflags = '%s -L%s' % (ldflags, os.path.join(llvm_clang_install, 'lib'))

        llvm_extra_env = {}
        llvm_extra_env['LD_LIBRARY_PATH'] = os.path.join(llvm_clang_install, 'lib')

        llvm_defines = {}

        self.llvm_compile_darwin_defines(llvm_defines)
        self.llvm_compile_linux_defines(llvm_defines, debug_build, no_lto, build_instrumented)
        
        if self.host_is_linux():
            ldflags += ' -lunwind --rtlib=compiler-rt -stdlib=libc++'

        if xunit_xml_output:
            llvm_defines['LLVM_LIT_ARGS'] = "--xunit-xml-output={} -sv".format(xunit_xml_output)

        if self.build_config.enable_assertions:
            llvm_defines['LLVM_ENABLE_ASSERTIONS'] = 'ON'

        if debug_build:
            llvm_defines['CMAKE_BUILD_TYPE'] = 'Debug'

        if build_instrumented:
            llvm_defines['LLVM_BUILD_INSTRUMENTED'] = 'ON'
            llvm_defines['LLVM_PROFDATA'] = llvm_profdata

            resource_dir = "lib/clang/10.0.1/lib/linux/libclang_rt.profile-x86_64.a"
            ldflags += ' %s' % os.path.join(llvm_clang_install, resource_dir)
        
        cflags = '-fstack-protector-strong -fPIE'
        ldflags += ' -Wl,-z,relro,-z,now -pie -s'

        self.llvm_compile_llvm_defines(llvm_defines, llvm_cc, llvm_cxx, cflags, ldflags)

        linker_path = os.path.abspath(os.path.join(self.build_config.REPOROOT_DIR, 'prebuilts', 'clang',
            'ohos', 'linux-x86_64', 'llvm', 'bin', 'ld.lld'))
        llvm_defines['CMAKE_LINKER'] = linker_path

        self.build_llvm(targets=self.build_config.TARGETS,
                        build_dir=llvm_path,
                        install_dir=out_dir,
                        build_name=build_name,
                        extra_defines=llvm_defines,
                        extra_env=llvm_extra_env)

    def llvm_compile_windows_defines(self,
                                     windows_defines,
                                     cc,
                                     cxx,
                                     windows_sysroot):

        if self.build_config.enable_assertions:
            
            windows_defines['LLVM_ENABLE_ASSERTIONS'] = 'ON'

        windows_defines['LLDB_RELOCATABLE_PYTHON'] = 'OFF'
        win_sysroot = self.merge_out_path(
            'clang_mingw', 'clang-%s' % self.build_config.CLANG_VERSION, 'x86_64-w64-mingw32')
        windows_defines['LLDB_ENABLE_PYTHON'] = 'ON'
        windows_defines['LLDB_PYTHON_HOME'] = 'python'
        windows_defines['LLDB_PYTHON_RELATIVE_PATH'] = 'bin/python/lib/python%s' % (self.build_config.LLDB_PY_VERSION)
        windows_defines['PYTHON_INCLUDE_DIRS'] = os.path.join(win_sysroot, 
                                        'include', 'python%s' % self.build_config.LLDB_PY_VERSION)
        windows_defines['PYTHON_LIBRARIES'] = os.path.join(win_sysroot, 'lib', 'libpython%s.dll.a'
                                                            % self.build_config.LLDB_PY_VERSION)
        windows_defines['SWIG_EXECUTABLE'] = self.find_program('swig')
        windows_defines['PYTHON_EXECUTABLE'] = self.find_program('python3')

        windows_defines['CMAKE_C_COMPILER'] = cc
        windows_defines['CMAKE_CXX_COMPILER'] = cxx
        windows_defines['CMAKE_SYSTEM_NAME'] = 'Windows'
        windows_defines['CMAKE_SYSTEM_PROCESSOR'] = 'x86_64'
        windows_defines['LLVM_BUILD_RUNTIME'] = 'OFF'
        windows_defines['LLVM_TOOL_CLANG_TOOLS_EXTRA_BUILD'] = 'ON'
        windows_defines['LLVM_TOOL_OPENMP_BUILD'] = 'OFF'
        windows_defines['LLVM_INCLUDE_TESTS'] = 'OFF'
        windows_defines['LLVM_ENABLE_LIBCXX'] = 'ON'
        windows_defines['LLVM_ENABLE_PROJECTS'] = 'clang;clang-tools-extra;lld;lldb'
        windows_defines['CLANG_BUILD_EXAMPLES'] = 'OFF'
        windows_defines['CMAKE_SYSROOT'] = windows_sysroot
        windows_defines['CMAKE_FIND_ROOT_PATH_MODE_INCLUDE'] = 'ONLY'
        windows_defines['CMAKE_FIND_ROOT_PATH_MODE_LIBRARY'] = 'ONLY'
        windows_defines['CMAKE_FIND_ROOT_PATH_MODE_PACKAGE'] = 'ONLY'
        windows_defines['CMAKE_FIND_ROOT_PATH_MODE_PROGRAM'] = 'NEVER'
        windows_defines['LLDB_ENABLE_LIBEDIT'] = 'OFF'
        windows_defines['LLDB_RELOCATABLE_PYTHON'] = 'OFF'

    def llvm_compile_windows_flags(self,
                                   windows_defines,
                                   compiler_rt_path,
                                   native_cmake_file_path,
                                   windowstool_path,
                                   windows64_install,
                                   ldflags,
                                   cflags):

        windows_defines['CROSS_TOOLCHAIN_FLAGS_NATIVE'] = ';'.join([
            '-DCMAKE_PREFIX_PATH=%s' % self.CMAKE_BIN_DIR,
            '-DCOMPILER_RT_BUILD_LIBFUZZER=OFF',
            '-DCMAKE_TOOLCHAIN_FILE=%s' % native_cmake_file_path,
            '-DLLVM_ENABLE_LIBCXX=ON',
            '-DCMAKE_BUILD_WITH_INSTALL_RPATH=TRUE',
            '-DCMAKE_INSTALL_RPATH=%s' % os.path.join(windowstool_path, 'lib')]
        )

        ldflag = ['-fuse-ld=lld',
                  '--rtlib=compiler-rt',
                  '-L%s' % compiler_rt_path,
                  '-lunwind', '-Wl,--dynamicbase',
                  '-Wl,--nxcompat',
                  '-lucrt',
                  '-lucrtbase',
                  '-lpthread',
                  '-L',
                  os.path.join(windows64_install, 'lib'),
                  '-Wl,--high-entropy-va']
        ldflags.extend(ldflag)

        cflag = ['-stdlib=libc++',
                 '--target=x86_64-pc-windows-gnu',
                 '-D_LARGEFILE_SOURCE',
                 '-D_FILE_OFFSET_BITS=64',
                 '-D_WIN32_WINNT=0x0600',
                 '-DWINVER=0x0600',
                 '-D__MSVCRT_VERSION__=0x1400',
                 '-DMS_WIN64']
        cflags.extend(cflag)

    def llvm_compile_windows_cmake(self,
                                   cflags,
                                   cxxflags,
                                   ldflags,
                                   windows_defines):
                                   

        zlib_path = self.merge_out_path('../', 'prebuilts', 'clang', 'host', 'windows-x86', 'toolchain-prebuilts',
                                        'zlib')
        zlib_inc = os.path.join(zlib_path, 'include')
        zlib_lib = os.path.join(zlib_path, 'lib')

        cflags.extend(['-I', zlib_inc])
        cxxflags.extend(['-I', zlib_inc])
        ldflags.extend(['-L', zlib_lib])

        windows_defines['CMAKE_ASM_FLAGS'] = ' '.join(cflags)
        windows_defines['CMAKE_C_FLAGS'] = ' '.join(cflags)
        windows_defines['CMAKE_CXX_FLAGS'] = ' '.join(cxxflags)
        windows_defines['CMAKE_EXE_LINKER_FLAGS'] = ' '.join(ldflags)
        windows_defines['CMAKE_SHARED_LINKER_FLAGS'] = ' '.join(ldflags)
        windows_defines['CMAKE_MODULE_LINKER_FLAGS'] = ' '.join(ldflags)


    def llvm_compile_for_windows(self,
                                 targets,
                                 enable_assertions,
                                 build_name):

        self.logger().info('Building llvm for windows.')

        build_dir = self.merge_out_path("windows-x86_64")
        windowstool_path = self.merge_out_path('clang_mingw', 'clang-%s' % self.build_config.CLANG_VERSION)
        windows64_install = self.merge_out_path('windows-x86_64-install')
        windows_sysroot = os.path.join(windowstool_path, 'x86_64-w64-mingw32')
        compiler_rt_path = self.merge_out_path('clang_mingw', 'clang-%s' % self.build_config.CLANG_VERSION,
            'lib', 'clang', self.build_config.CLANG_VERSION, 'lib', 'windows')

        self.check_create_dir(build_dir)

        # Write a NATIVE.cmake in windows_path that contains the compilers used
        # to build native tools such as llvm-tblgen and llvm-config.  This is
        # used below via the CMake variable CROSS_TOOLCHAIN_FLAGS_NATIVE.
        cc = os.path.join(windowstool_path, 'bin', 'clang')
        cxx = os.path.join(windowstool_path, 'bin', 'clang++')

        native_cmake_file_path = os.path.join(build_dir, 'NATIVE.cmake')
        native_cmake_text = ('set(CMAKE_C_COMPILER {cc})\n'
                             'set(CMAKE_CXX_COMPILER {cxx})\n'
                             'set(LLVM_ENABLE_PROJECTS "clang;lldb" CACHE STRING "" FORCE)\n'
                             'set(LLDB_ENABLE_PYTHON "OFF" CACHE STRING "" FORCE)\n'
                             'set(LLDB_DISABLE_CURSES "ON" CACHE STRING "" FORCE)\n'
                             'set(LLDB_ENABLE_LIBEDIT "OFF" CACHE STRING "" FORCE)\n'
                             ).format(cc=cc, cxx=cxx)

        with open(native_cmake_file_path, 'w') as native_cmake_file:
            native_cmake_file.write(native_cmake_text)

        # Extra cmake defines to use while building for Windows
        windows_defines = {}
        self.llvm_compile_windows_defines(windows_defines, cc, cxx, windows_sysroot)

        # Set CMake path, toolchain file for native compilation (to build tablegen
        # etc).  Also disable libfuzzer build during native compilation.

        ldflags = []
        cflags = []

        self.llvm_compile_windows_flags(windows_defines, compiler_rt_path, native_cmake_file_path, 
                                            windowstool_path, windows64_install, ldflags, cflags)

        cxxflags = list(cflags)

        windows_extra_env = dict()

        cxxflags.append('-fuse-cxa-atexit')
        cxxflags.extend(('-I', os.path.join(windows64_install, 'include', 'c++', 'v1')))

        self.llvm_compile_windows_cmake(cflags, cxxflags, ldflags, windows_defines)

        self.build_llvm(self.build_config.TARGETS,
                        build_dir,
                        windows64_install,
                        build_name,
                        extra_defines=windows_defines,
                        extra_env=windows_extra_env)


class SysrootComposer(BuildUtils):

    def __init__(self, build_config):
        super(SysrootComposer, self).__init__(build_config)

    def setup_cmake_platform(self, llvm_install):

        # OHOS.cmake already exsit on cmake prebuilts,
        # but it didn't contain these two lines, so we still need OHOS.cmake.
        ohos_cmake = 'OHOS.cmake'
        dst_dir = self.merge_out_path(
            '../prebuilts/cmake/%s/share/cmake-3.16/Modules/Platform' % self.platform_prefix())
        src_file = '%s/%s' % (self.build_config.CURRENT_DIR, ohos_cmake)
        if os.path.exists(os.path.join(dst_dir, ohos_cmake)):
            os.remove(os.path.join(dst_dir, ohos_cmake))
        shutil.copy2(src_file, dst_dir)


    def build_musl(self, llvm_install, target, *extra_args):
        cur_dir = os.getcwd()
        os.chdir(self.build_config.CURRENT_DIR)
        self.logger().info('build musl %s', self.merge_out_path('install'))
        args = ['./build_musl.sh', '-t', target, '-c',
                self.merge_out_path(llvm_install, 'bin'), '-o', self.merge_out_path()] + list(extra_args)
        self.check_call(args)
        os.chdir(cur_dir)

    def install_linux_headers(self, arch, target):
        dir_sufix = 'x86' if arch == 'x86_64' else arch
        linux_kernel_dir = os.path.join('kernel_linux_patches', 'linux-5.10')
        linux_kernel_path = os.path.join(self.build_config.OUT_PATH, '..', linux_kernel_dir)
        ohosmusl_sysroot_dst = self.merge_out_path('sysroot', target, 'usr')
        headers_tmp_dir = os.path.join(linux_kernel_path, 'prebuilts', 'usr', 'include')
        self.check_copy_tree(os.path.join(headers_tmp_dir, 'linux'),
                             os.path.join(ohosmusl_sysroot_dst, 'include/linux'))
        self.check_copy_tree(os.path.join(headers_tmp_dir, 'asm-%s' % dir_sufix,'asm'),
                             os.path.join(ohosmusl_sysroot_dst, 'include', 'asm'))
        self.check_copy_tree(os.path.join(headers_tmp_dir, 'asm-generic'),
                             os.path.join(ohosmusl_sysroot_dst, 'include/asm-generic'))

    def copy_libz_to_sysroot(self, libz_path, llvm_triple):
        # Install to sysroot
        dest_usr = self.merge_out_path('sysroot', llvm_triple, 'usr')
        dest_usr_include = os.path.join(dest_usr, 'include')

        # Copy over usr/include.
        zlib_h = self.merge_out_path('../third_party/zlib', 'zlib.h')
        self.check_copy_file(zlib_h, dest_usr_include)

        zconf_h = os.path.join(libz_path, 'zconf.h')
        self.check_copy_file(zconf_h, dest_usr_include)

        # Copy over usr/lib.
        dest_usr_lib = os.path.join(dest_usr, 'lib')
        static_zlib = os.path.join(libz_path, 'libz.a')
        self.check_copy_file(static_zlib, dest_usr_lib)


class LlvmLibs(BuildUtils):

    def __init__(self, build_config, sysroot_composer, llvm_package):
        super(LlvmLibs, self).__init__(build_config)
        self.sysroot_composer = sysroot_composer
        self.llvm_package = llvm_package

    def build_crt_libs(self, configs, llvm_install, need_lldb_server):
        for (arch, target) in configs:
            self.sysroot_composer.build_musl(llvm_install, target)
            if target.endswith(self.build_config.OPENHOS_SFX):
                self.sysroot_composer.install_linux_headers(arch, target)
            self.build_libs(need_lldb_server,
                                llvm_install,
                                target,
                                precompilation=True)          
            self.sysroot_composer.build_musl(llvm_install, target, '-l')
            
    def build_libs_defines(self,
                           llvm_triple,
                           defines,
                           cc,
                           cxx,
                           ar,
                           llvm_config,
                           ldflags,
                           cflags,
                           extra_flags):
        
        sysroot = self.merge_out_path('sysroot')

        defines['CMAKE_C_COMPILER'] = cc
        defines['CMAKE_CXX_COMPILER'] = cxx
        defines['CMAKE_AR'] = ar
        defines['LLVM_CONFIG_PATH'] = llvm_config
        defines['CMAKE_SYSROOT'] = sysroot
        defines['CMAKE_FIND_ROOT_PATH_MODE_INCLUDE'] = 'ONLY'
        defines['CMAKE_FIND_ROOT_PATH_MODE_LIBRARY'] = 'ONLY'
        defines['CMAKE_FIND_ROOT_PATH_MODE_PACKAGE'] = 'ONLY'
        defines['CMAKE_FIND_ROOT_PATH_MODE_PROGRAM'] = 'NEVER'

        ldflag = [
                '-Wl,-z,relro,-z,now',
                '-s',
                '-fuse-ld=lld',
                '-Wl,--gc-sections',
                '-Wl,--build-id=sha1',
                '-pie',
                '--rtlib=compiler-rt',
                '-stdlib=libc++',
                '-v', ]

        ldflags.extend(ldflag)

        cflag = [
                '-fstack-protector-strong',
                '-fPIE',
                '--target=%s' % llvm_triple,
                '-ffunction-sections',
                '-fdata-sections',
                extra_flags, ]
        
        cflags.extend(cflag)

    def build_need_libs_for_windows(self):

        self.build_libs_for_windows('libcxxabi', self.build_config.enable_assertions)
        self.build_libs_for_windows('libcxx', self.build_config.enable_assertions)
        self.build_libs_for_windows('libunwind', self.build_config.enable_assertions)

    def build_libs(self, need_lldb_server, llvm_install, llvm_build, precompilation=False):
        configs_list = [
            ('arm', self.liteos_triple('arm'), '-march=armv7-a -mfloat-abi=soft', ''),
            ('arm', self.liteos_triple('arm'), '-march=armv7-a -mcpu=cortex-a7 -mfloat-abi=soft', 'a7_soft'),
            ('arm', self.liteos_triple('arm'),
             '-march=armv7-a -mcpu=cortex-a7 -mfloat-abi=softfp -mfpu=neon-vfpv4', 'a7_softfp_neon-vfpv4'),
            ('arm', self.liteos_triple('arm'),
             '-march=armv7-a -mcpu=cortex-a7 -mfloat-abi=hard -mfpu=neon-vfpv4', 'a7_hard_neon-vfpv4'),

            ('arm', self.open_ohos_triple('arm'), '-march=armv7-a -mfloat-abi=soft', ''),
            ('arm', self.open_ohos_triple('arm'), '-march=armv7-a -mcpu=cortex-a7 -mfloat-abi=soft', 'a7_soft'),
            ('arm', self.open_ohos_triple('arm'),
             '-march=armv7-a -mcpu=cortex-a7 -mfloat-abi=softfp -mfpu=neon-vfpv4', 'a7_softfp_neon-vfpv4'),
            ('arm', self.open_ohos_triple('arm'),
             '-march=armv7-a -mcpu=cortex-a7 -mfloat-abi=hard -mfpu=neon-vfpv4', 'a7_hard_neon-vfpv4'),
            ('aarch64', self.open_ohos_triple('aarch64'), '', ''),
            ('x86_64', self.open_ohos_triple('x86_64'), '', ''),]

        cc = os.path.join(llvm_install, 'bin', 'clang')
        cxx = os.path.join(llvm_install, 'bin', 'clang++')
        ar = os.path.join(llvm_install, 'bin', 'llvm-ar')
        llvm_config = os.path.join(llvm_install, 'bin', 'llvm-config')
        seen_arch_list = [self.liteos_triple('arm')]

        self.set_clang_version(llvm_install)
        for (arch, llvm_triple, extra_flags, multilib_suffix) in configs_list:
            if llvm_build != llvm_triple:
                continue

            defines = {}
            ldflags = []
            cflags = []
            self.logger().info('Build libs for %s', llvm_triple)
            self.build_libs_defines(llvm_triple, defines, cc, cxx, ar, llvm_config, ldflags, cflags, extra_flags)

            llvm_path = self.merge_out_path('llvm_make')
            arch_list = [self.liteos_triple('arm'), self.open_ohos_triple('arm'), self.open_ohos_triple('aarch64'),
                         self.open_ohos_triple('x86_64')]
            if precompilation:
                self.build_crts(llvm_install, arch, llvm_triple, cflags, ldflags, multilib_suffix, defines)
                continue
            self.build_libcxxabi(llvm_install, ldflags, cflags, llvm_triple, arch, multilib_suffix, defines)
            self.build_libunwind(llvm_install, arch, llvm_triple, cflags, ldflags, multilib_suffix, defines)
            self.build_libcxx(llvm_install, arch, llvm_triple, cflags, ldflags, multilib_suffix, defines)
            self.build_libcxx(llvm_install, arch, llvm_triple, cflags, ldflags, multilib_suffix, defines,
                                  do_build_system=False)
            self.build_crts(llvm_install, arch, llvm_triple, cflags, ldflags, multilib_suffix, defines,
                                first_time=False)

            if llvm_triple in arch_list:
                if need_lldb_server and llvm_triple not in seen_arch_list:
                    self.build_lldb_server(llvm_install, llvm_path, arch, llvm_triple, cflags, ldflags,
                                                   defines)
                    seen_arch_list.append(llvm_triple)
                continue

            self.build_libomp(llvm_install, arch, llvm_triple, cflags, ldflags, multilib_suffix, defines)
            self.build_libz(arch, llvm_triple, cflags, ldflags, defines)
            if need_lldb_server and llvm_triple not in seen_arch_list:
                self.build_lldb_server(llvm_install, llvm_path, arch, llvm_triple, cflags, ldflags, defines)
                seen_arch_list.append(llvm_triple)

    def build_libcxxabi(self,
                        llvm_install,
                        ldflags,
                        cflags,
                        llvm_triple,
                        arch,
                        multilib_suffix,
                        defines):

        self.logger().info('Building libcxxabi for %s', arch)

        out_path = self.merge_out_path('lib', 'libcxxabi-%s' % llvm_triple)
        output_intdir = self.merge_out_path(out_path, 'lib')

        libcxxabi_cflags = list(cflags)

        libcxxabi_defines = defines.copy()
        libcxxabi_defines['CMAKE_EXE_LINKER_FLAGS'] = ' '.join(ldflags)
        libcxxabi_defines['CMAKE_SHARED_LINKER_FLAGS'] = ' '.join(ldflags)
        libcxxabi_defines['CMAKE_MODULE_LINKER_FLAGS'] = ' '.join(ldflags)

        libcxxabi_includes_str = os.path.abspath(os.path.join(self.build_config.LLVM_PROJECT_DIR, 'libcxx', 'include'))
        libcxxabi_defines['LIBCXXABI_LIBCXX_INCLUDES'] = libcxxabi_includes_str
        libcxxabi_defines['LIBCXXABI_ENABLE_SHARED'] = 'OFF'
        libcxxabi_defines['CMAKE_C_FLAGS'] = ' '.join(libcxxabi_cflags)
        libcxxabi_defines['CMAKE_CXX_FLAGS'] = ' '.join(libcxxabi_cflags)
        libcxxabi_defines['CMAKE_INSTALL_PREFIX'] = llvm_install

        libcxxabi_defines['OHOS'] = '1'
        libcxxabi_defines['LLVM_ENABLE_PER_TARGET_RUNTIME_DIR'] = 'ON'
        libcxxabi_defines['LLVM_TARGET_MULTILIB_SUFFIX'] = multilib_suffix
        libcxxabi_defines['COMPILER_RT_USE_BUILTINS_LIBRARY'] = 'ON'
        libcxxabi_defines['CMAKE_SYSTEM_NAME'] = 'OHOS'
        libcxxabi_defines['CMAKE_CROSSCOMPILING'] = 'True'
        libcxxabi_defines['LLVM_DEFAULT_TARGET_TRIPLE'] = llvm_triple
        libcxxabi_defines['LLVM_LIBRARY_OUTPUT_INTDIR'] = output_intdir

        libcxxabi_defines['CMAKE_TRY_COMPILE_TARGET_TYPE'] = 'STATIC_LIBRARY'
        libcxxabi_defines['LIBCXXABI_HAS_CXA_THREAD_ATEXIT_IMPL'] = 'OFF'

        self.check_rm_tree(out_path)
        cmake_libcxxabi = os.path.abspath(os.path.join(self.build_config.LLVM_PROJECT_DIR, 'libcxxabi'))

        self.invoke_cmake(cmake_libcxxabi,
                          out_path,
                          libcxxabi_defines,
                          env=dict(self.build_config.ORIG_ENV))

        self.invoke_ninja(out_path=out_path,
                          env=dict(self.build_config.ORIG_ENV),
                          target=None,
                          install=True)

    def build_crts(self,
                   llvm_install,
                   arch,
                   llvm_triple,
                   cflags,
                   ldflags,
                   multilib_suffix,
                   defines,
                   first_time=True):

        self.logger().info('Building compiler-rt for %s', arch)

        suffix = '-' + multilib_suffix if multilib_suffix else ''
        crt_path = self.merge_out_path('lib', 'clangrt-%s%s' % (llvm_triple, suffix))
        crt_install = os.path.join(llvm_install, 'lib', 'clang', self.build_config.VERSION)

        crt_extra_flags = []
        if not self.build_config.debug:
            # Remove absolute paths from compiler-rt debug info emitted with -gline-tables-only
            crt_extra_flags = ['-ffile-prefix-map=%s=.' % self.build_config.REPOROOT_DIR]

        crt_defines = defines.copy()
        crt_defines.update(self.base_cmake_defines())
        crt_defines['CMAKE_EXE_LINKER_FLAGS'] = ' '.join(ldflags)
        crt_defines['CMAKE_SHARED_LINKER_FLAGS'] = ' '.join(ldflags)
        crt_defines['CMAKE_MODULE_LINKER_FLAGS'] = ' '.join(ldflags)
        crt_defines['CMAKE_C_FLAGS'] = ' '.join(cflags + crt_extra_flags)
        crt_defines['CMAKE_ASM_FLAGS'] = ' '.join(cflags + crt_extra_flags)
        crt_defines['CMAKE_CXX_FLAGS'] = ' '.join(cflags + crt_extra_flags)
        crt_defines['COMPILER_RT_TEST_COMPILER_CFLAGS'] = ' '.join(cflags)
        crt_defines['OHOS'] = '1'
        crt_defines['COMPILER_RT_TEST_TARGET_TRIPLE'] = llvm_triple
        crt_defines['COMPILER_RT_INCLUDE_TESTS'] = 'ON'
        crt_defines['CMAKE_INSTALL_PREFIX'] = crt_install
        crt_defines['LLVM_TARGET_MULTILIB_SUFFIX'] = multilib_suffix
        if first_time or llvm_triple == self.liteos_triple('arm'):
            crt_defines['COMPILER_RT_BUILD_LIBFUZZER'] = 'OFF'
        else:
            crt_defines['COMPILER_RT_BUILD_LIBFUZZER'] = 'ON'
        crt_defines['LLVM_ENABLE_PER_TARGET_RUNTIME_DIR'] = 'ON'
        crt_defines['COMPILER_RT_USE_BUILTINS_LIBRARY'] = 'ON'
        crt_defines['CMAKE_SYSTEM_NAME'] = 'OHOS'
        crt_defines['CMAKE_CROSSCOMPILING'] = 'True'
        crt_defines['SANITIZER_CXX_ABI'] = 'libcxxabi'
        crt_defines['CMAKE_TRY_COMPILE_TARGET_TYPE'] = 'STATIC_LIBRARY'
        crt_defines['COMPILER_RT_HWASAN_WITH_INTERCEPTORS'] = 'OFF'

        if not llvm_triple == self.liteos_triple('arm'):
            crt_defines['COMPILER_RT_EXCLUDE_ATOMIC_BUILTIN'] = 'OFF'
            if first_time:
                crt_defines['COMPILER_RT_BUILD_SANITIZERS'] = 'OFF'
            else:
                crt_defines['COMPILER_RT_BUILD_SANITIZERS'] = 'ON'
                crt_defines['COMPILER_RT_DEFAULT_TARGET_TRIPLE'] = llvm_triple
        else:
            crt_defines['COMPILER_RT_BUILD_SANITIZERS'] = 'OFF'

        crt_cmake_path = os.path.abspath(os.path.join(self.build_config.LLVM_PROJECT_DIR, 'compiler-rt'))
        self.rm_cmake_cache(crt_path)

        self.invoke_cmake(crt_cmake_path,
                          crt_path,
                          crt_defines,
                          env=dict(self.build_config.ORIG_ENV))

        self.invoke_ninja(out_path=crt_path,
                          env=dict(self.build_config.ORIG_ENV),
                          target=None,
                          install=True)

    def build_libunwind(self,
                        llvm_install,
                        arch,
                        llvm_triple,
                        cflags,
                        ldflags,
                        multilib_suffix,
                        defines):

        self.logger().info('Building libunwind for %s ', arch)

        lib_path = self.merge_out_path('lib', 'libunwind-%s' % llvm_triple)
        out_dir = os.path.join(lib_path, 'lib')

        lib_cflags = list(cflags)
        lib_cflags.append('-fPIC')

        lib_defines = defines.copy()
        lib_defines.update(self.base_cmake_defines())
        lib_defines['CMAKE_EXE_LINKER_FLAGS'] = ' '.join(ldflags)
        lib_defines['CMAKE_SHARED_LINKER_FLAGS'] = ' '.join(ldflags)
        lib_defines['CMAKE_MODULE_LINKER_FLAGS'] = ' '.join(ldflags)
        lib_defines['OHOS'] = '1'
        lib_defines['CMAKE_ASM_FLAGS'] = ' '.join(lib_cflags)
        lib_defines['CMAKE_C_FLAGS'] = ' '.join(lib_cflags)
        lib_defines['CMAKE_CXX_FLAGS'] = ' '.join(lib_cflags)
        lib_defines['CMAKE_INSTALL_PREFIX'] = llvm_install
        lib_defines['LLVM_ENABLE_PER_TARGET_RUNTIME_DIR'] = 'ON'
        lib_defines['LLVM_TARGET_MULTILIB_SUFFIX'] = multilib_suffix
        lib_defines['COMPILER_RT_USE_BUILTINS_LIBRARY'] = 'ON'
        lib_defines['CMAKE_SYSTEM_NAME'] = 'OHOS'
        lib_defines['CMAKE_CROSSCOMPILING'] = 'True'
        lib_defines['LLVM_DEFAULT_TARGET_TRIPLE'] = llvm_triple
        lib_defines['LLVM_LIBRARY_OUTPUT_INTDIR'] = out_dir
        lib_defines['LLVM_ENABLE_LIBCXX'] = 'ON'
        lib_defines['LIBUNWIND_ENABLE_SHARED'] = 'OFF'
        lib_defines['CMAKE_TRY_COMPILE_TARGET_TYPE'] = 'STATIC_LIBRARY'
        lib_defines['_LIBUNWIND_USE_DLADDR'] = 'OFF'
        lib_cmake_path = os.path.abspath(os.path.join(self.build_config.LLVM_PROJECT_DIR, 'libunwind'))

        self.rm_cmake_cache(lib_path)

        self.invoke_cmake(lib_cmake_path,
                          lib_path,
                          lib_defines,
                          env=dict(self.build_config.ORIG_ENV))

        self.invoke_ninja(out_path=lib_path,
                          env=dict(self.build_config.ORIG_ENV),
                          target=None,
                          install=True)

    def build_libcxx_defines_parameters(self,
                                        libcxx_defines, 
                                        libcxx_ldflags,
                                        libcxx_cflags, 
                                        llvm_install, 
                                        multilib_suffix,
                                        libcxx_path,  
                                        llvm_triple, 
                                        out_dir,
                                        libcxx_ndk_install,
                                        do_build_system):
    
        libcxx_defines['CMAKE_EXE_LINKER_FLAGS'] = ' '.join(libcxx_ldflags)
        libcxx_defines['CMAKE_SHARED_LINKER_FLAGS'] = ' '.join(libcxx_ldflags)
        libcxx_defines['CMAKE_MODULE_LINKER_FLAGS'] = ' '.join(libcxx_ldflags)
        libcxx_defines['CMAKE_ASM_FLAGS'] = ' '.join(libcxx_cflags)
        libcxx_defines['CMAKE_C_FLAGS'] = ' '.join(libcxx_cflags)
        libcxx_defines['CMAKE_CXX_FLAGS'] = ' '.join(libcxx_cflags)
        libcxx_defines['CMAKE_BUILD_TYPE'] = 'Release'
        if do_build_system:
            libcxx_defines['LIBCXX_ABI_NAMESPACE'] = '__h'
            libcxx_defines['CMAKE_INSTALL_PREFIX'] = llvm_install
            libcxx_defines['LIBCXX_INSTALL_HEADER_PREFIX'] = os.path.join(llvm_install, 'include', 'libcxx-ohos', '')
        else:
            libcxx_defines['LIBCXX_ABI_NAMESPACE'] = '__n1'
            libcxx_defines['CMAKE_INSTALL_PREFIX'] = libcxx_ndk_install
            libcxx_defines['LIBCXX_INSTALL_HEADER_PREFIX'] = os.path.join(libcxx_ndk_install, 'include', 'libcxx-ohos', '')
            libcxx_defines['LIBCXX_OUTPUT_NAME'] = 'c++_shared'
            libcxx_defines['LIBCXX_OUTPUT_STATIC_NAME'] = 'c++_static'

        libcxx_defines['LIBCXX_HEADER_DIR'] = libcxx_path
        libcxx_defines['OHOS'] = '1'
        libcxx_defines['LLVM_ENABLE_PER_TARGET_RUNTIME_DIR'] = 'ON'
        libcxx_defines['LLVM_TARGET_MULTILIB_SUFFIX'] = multilib_suffix
        libcxx_defines['COMPILER_RT_USE_BUILTINS_LIBRARY'] = 'ON'
        libcxx_defines['CMAKE_SYSTEM_NAME'] = 'OHOS'
        libcxx_defines['CMAKE_CROSSCOMPILING'] = 'True'
        libcxx_defines['LLVM_DEFAULT_TARGET_TRIPLE'] = llvm_triple
        libcxx_defines['LLVM_LIBRARY_OUTPUT_INTDIR'] = out_dir
        libcxx_defines['LIBCXX_ENABLE_STATIC_ABI_LIBRARY'] = 'ON'
        libcxx_defines['LIBCXX_CXX_ABI'] = 'libcxxabi'
        libcxx_defines['LIBCXX_CXX_ABI_INCLUDE_PATHS'] = os.path.abspath(
            os.path.join(self.build_config.LLVM_PROJECT_DIR, 'libcxxabi', 'include'))
        str_cpp = 'c++'
        libcxx_defines['LIBCXX_CXX_ABI_LIBRARY_PATH'] = os.path.join(llvm_install, 'lib', llvm_triple, str_cpp)
        libcxx_defines['LIBCXX_USE_COMPILER_RT'] = 'ON'
        libcxx_defines['LIBCXX_HAS_PTHREAD_LIB'] = 'OFF'
        libcxx_defines['LIBCXX_HAS_RT_LIB'] = 'OFF'
        libcxx_defines['CMAKE_TRY_COMPILE_TARGET_TYPE'] = 'STATIC_LIBRARY'
        
        


    def build_libcxx(self,
                     llvm_install,
                     arch,
                     llvm_triple,
                     cflags,
                     ldflags,
                     multilib_suffix,
                     defines,
                     do_build_system=True):

        self.logger().info('Building libcxx for %s', arch)

        suffix = '-' + multilib_suffix if multilib_suffix else ''
        libcxx_path = self.merge_out_path('lib', 'libcxx-%s-%s%s' % ('system' if do_build_system else 'ndk',
                                                                   llvm_triple, suffix))
       
        out_dir = os.path.join(libcxx_path, 'lib')
        libcxx_ndk_install = self.merge_out_path('libcxx-ndk')
        self.check_create_dir(libcxx_ndk_install)

        libcxx_ldflags = list(ldflags)
        libcxx_cflags = list(cflags)
        libcxx_ldflags.append('-lunwind')
        libcxx_cflags.append('-fPIC')

        libcxx_defines = defines.copy()
        
        self.build_libcxx_defines_parameters(libcxx_defines, 
                                            libcxx_ldflags,
                                            libcxx_cflags, 
                                            llvm_install,
                                            multilib_suffix,
                                            libcxx_path,  
                                            llvm_triple, 
                                            out_dir,
                                            libcxx_ndk_install,
                                            do_build_system)

        libcxx_cmake_path = os.path.abspath(os.path.join(self.build_config.LLVM_PROJECT_DIR, 'libcxx'))
        self.rm_cmake_cache(libcxx_path)

        self.invoke_cmake(libcxx_cmake_path,
                          libcxx_path,
                          libcxx_defines,
                          env=dict(self.build_config.ORIG_ENV))

        self.invoke_ninja(out_path=libcxx_path,
                          env=dict(self.build_config.ORIG_ENV),
                          target=None,
                          install=True)

    def build_libomp(self,
                     llvm_install,
                     arch,
                     llvm_triple,
                     cflags,
                     ldflags,
                     multilib_suffix,
                     defines):

        self.logger().info('Building libomp for %s', arch)

        libomp_path = self.merge_out_path('lib', 'libomp-%s' % llvm_triple)
        out_dir = os.path.join(libomp_path, 'lib')

        libomp_cflags = list(cflags)
        libomp_cflags.append('-fPIC')

        libomp_defines = defines.copy()
        libomp_defines.update(self.base_cmake_defines())
        libomp_defines['CMAKE_EXE_LINKER_FLAGS'] = ' '.join(ldflags)
        libomp_defines['CMAKE_SHARED_LINKER_FLAGS'] = ' '.join(ldflags)
        libomp_defines['CMAKE_MODULE_LINKER_FLAGS'] = ' '.join(ldflags)

        libomp_defines['OHOS'] = '1'
        libomp_defines['CMAKE_ASM_FLAGS'] = ' '.join(libomp_cflags)
        libomp_defines['CMAKE_C_FLAGS'] = ' '.join(libomp_cflags)
        libomp_defines['CMAKE_CXX_FLAGS'] = ' '.join(libomp_cflags)
        libomp_defines['OPENMP_ENABLE_LIBOMPTARGET'] = 'FALSE'

        libomp_defines['OPENMP_LIBDIR_SUFFIX'] = os.path.join(os.sep, llvm_triple, multilib_suffix)
        libomp_defines['LIBOMP_ENABLE_SHARED'] = 'FALSE'
        libomp_defines['CMAKE_POLICY_DEFAULT_CMP0056'] = 'NEW'
        libomp_defines['CMAKE_INSTALL_PREFIX'] = llvm_install
        libomp_defines['COMPILER_RT_USE_BUILTINS_LIBRARY'] = 'ON'
        libomp_defines['CMAKE_SYSTEM_NAME'] = 'OHOS'
        libomp_defines['CMAKE_CROSSCOMPILING'] = 'True'
        libomp_defines['LLVM_LIBRARY_OUTPUT_INTDIR'] = out_dir
        libomp_defines['LLVM_ENABLE_LIBCXX'] = 'ON'
        libomp_defines['CMAKE_TRY_COMPILE_TARGET_TYPE'] = 'STATIC_LIBRARY'

        libomp_cmake_path = os.path.join(self.build_config.LLVM_PROJECT_DIR, 'openmp')
        self.rm_cmake_cache(libomp_path)

        self.invoke_cmake(libomp_cmake_path,
                          libomp_path,
                          libomp_defines,
                          env=dict(self.build_config.ORIG_ENV))

        self.invoke_ninja(out_path=libomp_path,
                          env=dict(self.build_config.ORIG_ENV),
                          target=None,
                          install=True)

    def build_libz(self,
                   arch,
                   llvm_triple,
                   cflags,
                   ldflags,
                   defines):

        self.logger().info('Building libz for %s ', arch)

        libz_path = self.merge_out_path('lib', 'libz-%s' % llvm_triple)
        if llvm_triple == self.hos_triple('arm'):
            ldflags.append('-lunwind')

        libz_cflags = list(cflags)
        libz_cflags.append('-fPIC')

        libz_defines = defines.copy()
        libz_defines.update(self.base_cmake_defines())
        libz_defines['CMAKE_EXE_LINKER_FLAGS'] = ' '.join(ldflags)
        libz_defines['CMAKE_SHARED_LINKER_FLAGS'] = ' '.join(ldflags)
        libz_defines['CMAKE_MODULE_LINKER_FLAGS'] = ' '.join(ldflags)
        libz_defines['OHOS'] = '1'
        libz_defines['CMAKE_ASM_FLAGS'] = ' '.join(libz_cflags)
        libz_defines['CMAKE_C_FLAGS'] = ' '.join(libz_cflags)
        libz_defines['CMAKE_CXX_FLAGS'] = ' '.join(libz_cflags)
        libz_defines['CMAKE_TRY_COMPILE_TARGET_TYPE'] = 'STATIC_LIBRARY'

        libz_cmake_path = os.path.join(self.build_config.REPOROOT_DIR, 'third_party/zlib')
        self.rm_cmake_cache(libz_path)

        self.invoke_cmake(libz_cmake_path,
                          libz_path,
                          libz_defines,
                          env=dict(self.build_config.ORIG_ENV))

        self.invoke_ninja(out_path=libz_path,
                          env=dict(self.build_config.ORIG_ENV),
                          target=None,
                          install=False)

        self.sysroot_composer.copy_libz_to_sysroot(libz_path, llvm_triple)

    def build_lldb_server(self,
                          llvm_install,
                          llvm_path,
                          arch,
                          llvm_triple,
                          cflags,
                          ldflags,
                          defines):

        self.logger().info('Building lldb for %s', arch)

        lldb_path = self.merge_out_path('lib', 'lldb-server-%s' % llvm_triple)
        crt_install = os.path.join(llvm_install, 'lib', 'clang', self.build_config.VERSION)
        out_dir = os.path.join(lldb_path, 'bin')

        lldb_ldflags = list(ldflags)
        lldb_cflags = list(cflags)

        lldb_ldflags.append('-lunwind')
        lldb_ldflags.append('-static')

        lldb_defines = defines.copy()
        lldb_defines.update(self.base_cmake_defines())

        lldb_defines['CMAKE_EXE_LINKER_FLAGS'] = ' '.join(lldb_ldflags)
        lldb_defines['CMAKE_SHARED_LINKER_FLAGS'] = ' '.join(lldb_ldflags)
        lldb_defines['CMAKE_MODULE_LINKER_FLAGS'] = ' '.join(lldb_ldflags)

        lldb_defines['OHOS'] = '1'
        lldb_defines['CMAKE_C_FLAGS'] = ' '.join(lldb_cflags)
        lldb_defines['CMAKE_ASM_FLAGS'] = ' '.join(lldb_cflags)
        lldb_defines['CMAKE_CXX_FLAGS'] = ' '.join(lldb_cflags)
        lldb_defines['CMAKE_INSTALL_PREFIX'] = crt_install
        lldb_defines['LLVM_ENABLE_PER_TARGET_RUNTIME_DIR'] = 'ON'
        lldb_defines['COMPILER_RT_USE_BUILTINS_LIBRARY'] = 'ON'
        lldb_defines['CMAKE_SYSTEM_NAME'] = 'OHOS'
        lldb_defines['CMAKE_CROSSCOMPILING'] = 'True'
        lldb_defines['LLVM_DEFAULT_TARGET_TRIPLE'] = llvm_triple
        lldb_defines['LLVM_LIBRARY_OUTPUT_INTDIR'] = out_dir
        lldb_defines['LLVM_ENABLE_LIBCXX'] = 'ON'
        lldb_defines['LLVM_ENABLE_PROJECTS'] = 'clang;lldb'
        lldb_defines['LLVM_TABLEGEN'] = os.path.join(llvm_install, 'bin', 'llvm-tblgen')
        lldb_defines['CLANG_TABLEGEN'] = os.path.join(llvm_install, '..', llvm_path, 'bin', 'clang-tblgen')
        lldb_defines['LLDB_TABLEGEN'] = os.path.join(llvm_install, '..', llvm_path, 'bin', 'lldb-tblgen')
        lldb_defines['LLVM_HOST_TRIPLE'] = llvm_triple
        lldb_defines['LLVM_TARGET_ARCH'] = arch
        lldb_defines['LLVM_TARGETS_TO_BUILD'] = self.build_config.TARGETS

        lldb_cmake_path = os.path.join(self.build_config.LLVM_PROJECT_DIR, 'llvm')

        self.invoke_cmake(lldb_cmake_path,
                          lldb_path,
                          lldb_defines,
                          env=dict(self.build_config.ORIG_ENV))

        self.invoke_ninja(out_path=lldb_path,
                          env=dict(self.build_config.ORIG_ENV),
                          target='lldb-server',
                          install=False)

        self.llvm_package.copy_lldb_server_to_llvm_install(lldb_path, crt_install, llvm_triple)

    @staticmethod
    def build_libs_windows_cmake_defines(cmake_defines, toolchain_dir, windows_sysroot):
        cmake_defines['CMAKE_C_COMPILER_WORKS'] = '1'
        cmake_defines['CMAKE_CXX_COMPILER_WORKS'] = '1'
        cmake_defines['CMAKE_SYSTEM_NAME'] = 'Windows'
        cmake_defines['CMAKE_SYSTEM_PROCESSOR'] = 'x86_64'
        cmake_defines['CMAKE_C_COMPILER'] = os.path.join(toolchain_dir, 'bin', 'clang')
        cmake_defines['CMAKE_CXX_COMPILER'] = os.path.join(toolchain_dir, 'bin', 'clang++')
        cmake_defines['LLVM_CONFIG_PATH'] = os.path.join(toolchain_dir, 'bin', 'llvm-config')
        cmake_defines['LLVM_ENABLE_LIBCXX'] = 'ON'
        cmake_defines['CMAKE_SYSROOT'] = windows_sysroot
        cmake_defines['CMAKE_FIND_ROOT_PATH_MODE_INCLUDE'] = 'ONLY'
        cmake_defines['CMAKE_FIND_ROOT_PATH_MODE_LIBRARY'] = 'ONLY'
        cmake_defines['CMAKE_FIND_ROOT_PATH_MODE_PACKAGE'] = 'ONLY'
        cmake_defines['CMAKE_FIND_ROOT_PATH_MODE_PROGRAM'] = 'NEVER'


    def build_libs_for_windows(self, libname, enable_assertions):

        self.logger().info('Building libs for windows.')
        toolchain_dir = self.merge_out_path('clang_mingw', 'clang-%s' % self.build_config.CLANG_VERSION)
        install_dir = self.merge_out_path('windows-x86_64-install')
        compiler_rt_path = self.merge_out_path('clang_mingw', 'clang-%s' % self.build_config.CLANG_VERSION, 'lib', 'clang', 
                                                self.build_config.CLANG_VERSION, 'lib', 'windows')
        windows_sysroot = self.merge_out_path('clang_mingw', 'clang-%s' % self.build_config.CLANG_VERSION, 'x86_64-w64-mingw32')

        ldflags = ['-fuse-ld=lld', '--rtlib=compiler-rt', '-L%s' % compiler_rt_path, '-lunwind']
        cflags = ['-stdlib=libc++', '--target=x86_64-pc-windows-gnu', '-D_LARGEFILE_SOURCE',
                  '-D_FILE_OFFSET_BITS=64', '-D_WIN32_WINNT=0x0600', '-DWINVER=0x0600', '-D__MSVCRT_VERSION__=0x1400']

        cmake_defines = {}
        self.build_libs_windows_cmake_defines(cmake_defines, toolchain_dir, windows_sysroot)

        # Build only the static library.
        cmake_defines['%s_ENABLE_SHARED' % libname.upper()] = 'OFF'
        if self.build_config.enable_assertions:
            cmake_defines['%s_ENABLE_ASSERTIONS' % libname.upper()] = 'ON'

        if libname == 'libcxx':
            cmake_defines['LIBCXX_ENABLE_STATIC_ABI_LIBRARY'] = 'ON'
            cmake_defines['LIBCXX_CXX_ABI'] = 'libcxxabi'
            cmake_defines['LIBCXX_HAS_WIN32_THREAD_API'] = 'ON'
            cmake_defines['LIBCXX_SUPPORTS_STD_EQ_CXX11_FLAG'] = 'TRUE'
            cmake_defines['LIBCXX_CXX_ABI_INCLUDE_PATHS'] = os.path.abspath(
                os.path.join(self.build_config.LLVM_PROJECT_DIR, 'libcxxabi', 'include'))
            cmake_defines['LIBCXX_CXX_ABI_LIBRARY_PATH'] = os.path.join(install_dir, 'lib')
            cflags.append('-D_LIBCXXABI_DISABLE_VISIBILITY_ANNOTATIONS')

        elif libname == 'libcxxabi':
            cmake_defines['LIBCXXABI_LIBCXX_INCLUDES'] = os.path.abspath(
                os.path.join(self.build_config.LLVM_PROJECT_DIR, 'libcxx', 'include'))
            cflags.append('-D_LIBCPP_DISABLE_VISIBILITY_ANNOTATIONS')
            cflags.append('-D_LIBCPP_HAS_THREAD_API_WIN32')

        elif libname == 'libunwind':
            cmake_defines['CXX_SUPPORTS_CXX11'] = 'TRUE'
            cmake_defines['CXX_SUPPORTS_CXX_STD'] = 'TRUE'

        cmake_defines['CMAKE_INSTALL_PREFIX'] = install_dir
        cmake_defines['CMAKE_C_FLAGS'] = ' '.join(cflags)
        cmake_defines['CMAKE_CXX_FLAGS'] = ' '.join(cflags)
        cmake_defines['CMAKE_EXE_LINKER_FLAGS'] = ' '.join(ldflags)
        cmake_defines['CMAKE_SHARED_LINKER_FLAGS'] = ' '.join(ldflags)
        cmake_defines['CMAKE_MODULE_LINKER_FLAGS'] = ' '.join(ldflags)

        out_path = self.merge_out_path('lib', 'windows-%s' % libname)
        self.check_rm_tree(out_path)

        libname_cmake_path = os.path.abspath(os.path.join(self.build_config.LLVM_PROJECT_DIR, libname))

        self.invoke_cmake(libname_cmake_path,
                          out_path,
                          cmake_defines,
                          env=dict(self.build_config.ORIG_ENV))

        self.invoke_ninja(out_path,
                          env=dict(self.build_config.ORIG_ENV),
                          install=True)


class LldbMi(BuildUtils):

    def __init__(self, build_config, llvm_package):
        super(LldbMi, self).__init__(build_config)
        self.llvm_package = llvm_package

    def build_lldb_mi(self, llvm_install):
        self.logger().info('Building lldb-mi for linux.')

        llvm_path = os.path.abspath(os.path.join(self.build_config.REPOROOT_DIR,
                                                 'prebuilts/clang/ohos', self.use_platform(), 'clang-%s' % self.build_config.CLANG_VERSION))
        lldb_mi_cmake_path = os.path.join(self.build_config.LLVM_PROJECT_DIR, '../lldb-mi')
        lldb_mi_path = self.merge_out_path('lldb_mi_build')

        self.check_rm_tree(lldb_mi_path)
        self.rm_cmake_cache(lldb_mi_path)

        if self.host_is_darwin():
            cflags = []
            cxxflags =[]
            ldflags = ['-Wl,-rpath,%s' % '@loader_path/../lib']
        else:
            cflags = []
            cxxflags =[]
            ldflags = ['-fuse-ld=lld', '-Wl,-rpath,%s' % '\$ORIGIN/../lib']

        ldflags.append('-L%s' % os.path.join(llvm_path, 'lib'))
        cxxflags.append('-std=c++14')
        cxxflags.append('-fstack-protector-strong -fPIE -v')
        cflags.append('-fstack-protector-strong -fPIE -v')
        ldflags.append('-Wl,-z,relro,-z,now -pie -s')

        lldb_mi_defines = {}
        lldb_mi_defines['CMAKE_C_COMPILER'] = os.path.join(llvm_path, 'bin', 'clang')
        lldb_mi_defines['CMAKE_CXX_COMPILER'] = os.path.join(llvm_path, 'bin', 'clang++')
        lldb_mi_defines['CMAKE_C_FLAGS'] = ' '.join(cflags)
        lldb_mi_defines['CMAKE_CXX_FLAGS'] = ' '.join(cxxflags)
        lldb_mi_defines['CMAKE_EXE_LINKER_FLAGS'] = ' '.join(ldflags)
        lldb_mi_defines['CMAKE_SHARED_LINKER_FLAGS'] = ' '.join(ldflags)
        lldb_mi_defines['CMAKE_MODULE_LINKER_FLAGS'] = ' '.join(ldflags)
        lldb_mi_defines['LLVM_DIR'] = os.path.join(llvm_install, 'lib/cmake/llvm/')
        lldb_mi_defines['LLVM_ENABLE_LIBCXX'] = 'ON'
        lldb_mi_defines['CMAKE_INSTALL_PREFIX'] = llvm_install

        self.invoke_cmake(lldb_mi_cmake_path,
                          lldb_mi_path,
                          lldb_mi_defines,
                          env=dict(self.build_config.ORIG_ENV))

        self.invoke_ninja(lldb_mi_path,
                          env=dict(self.build_config.ORIG_ENV),
                          install=False)

        self.llvm_package.copy_lldb_mi_to_llvm_install(lldb_mi_path, llvm_install)

    @staticmethod
    def ldflags_lists_define():
        ldflags_lists = ['-Wl,--dynamicbase',
                    '-Wl,--nxcompat',
                    '-lucrt',
                    '-lucrtbase',
                    '-lpthread',
                    '-L', ]
        return ldflags_lists

    @staticmethod
    def cflags_lists_define():               
        cflags_lists = ['-D_LARGEFILE_SOURCE',
                    '-D_FILE_OFFSET_BITS=64',
                    '-D_WIN32_WINNT=0x0600',
                    '-DWINVER=0x0600',
                    '-D__MSVCRT_VERSION__=0x1400',
                    '-DMS_WIN64', ]
        return cflags_lists

    # Extra cmake defines to use while building for Windows
    def lldb_mi_windefines_dictionary(self, cc, cxx, windows_sysroot, cflags, cxxflags, ldflags):
        lldb_mi_windefines = {}
        lldb_mi_windefines['CMAKE_C_COMPILER'] = cc
        lldb_mi_windefines['CMAKE_CXX_COMPILER'] = cxx
        lldb_mi_windefines['CMAKE_C_COMPILER_WORKS'] = '1'
        lldb_mi_windefines['CMAKE_CXX_COMPILER_WORKS'] = '1'
        lldb_mi_windefines['CMAKE_SYSTEM_NAME'] = 'Windows'
        lldb_mi_windefines['CMAKE_SYSTEM_PROCESSOR'] = 'x86_64'
        lldb_mi_windefines['LLVM_ENABLE_LIBCXX'] = 'ON'
        lldb_mi_windefines['CMAKE_SYSROOT'] = windows_sysroot
        lldb_mi_windefines['LLVM_INSTALL_PREFIX'] = self.merge_out_path('windows-x86_64-install')
        lldb_mi_windefines['LLVM_DIR'] = self.merge_out_path('windows-x86_64-install/lib/cmake/llvm/')
        lldb_mi_windefines['CMAKE_C_FLAGS'] = ' '.join(cflags)
        lldb_mi_windefines['CMAKE_CXX_FLAGS'] = ' '.join(cxxflags)
        lldb_mi_windefines['CMAKE_EXE_LINKER_FLAGS'] = ' '.join(ldflags)
        lldb_mi_windefines['CMAKE_SHARED_LINKER_FLAGS'] = ' '.join(ldflags)
        lldb_mi_windefines['CMAKE_MODULE_LINKER_FLAGS'] = ' '.join(ldflags)

        if self.build_config.enable_assertions:
            lldb_mi_windefines['LLVM_ENABLE_ASSERTIONS'] = 'ON'

        return lldb_mi_windefines


    def build_lldb_mi_for_windows(self):
        self.logger().info('Building lldb-mi for windows.')

        build_dir = self.merge_out_path('clang_mingw', 'clang-%s' % self.build_config.CLANG_VERSION)
        lldb_mi_windows64_path = self.merge_out_path('windows-x86_64-lldb-mi')
        lldb_mi_cmake_path = os.path.abspath(os.path.join(self.build_config.LLVM_PROJECT_DIR, '../lldb-mi'))
        windows64_install = self.merge_out_path('windows-x86_64-install')
        cc = os.path.join(build_dir, 'bin', 'clang')
        cxx = os.path.join(build_dir, 'bin', 'clang++')

        windows_sysroot = self.merge_out_path('clang_mingw', 'clang-%s' % self.build_config.CLANG_VERSION, 'x86_64-w64-mingw32')
        self.check_create_dir(lldb_mi_windows64_path)
        compiler_rt_path = self.merge_out_path('clang_mingw', 'clang-%s' % self.build_config.CLANG_VERSION,
             'lib', 'clang', self.build_config.CLANG_VERSION, 'lib', 'windows')

        ldflags = ['-fuse-ld=lld',
                   '--rtlib=compiler-rt',
                   '-L%s' % compiler_rt_path,
                   '-lunwind',
                   '-L%s' % os.path.join(windows64_install, 'lib'),
                   ]
        ldflags.extend(self.ldflags_lists_define())
        ldflags.append(os.path.join(windows64_install, 'lib'))
        ldflags.append('-Wl,--high-entropy-va')


        cflags = ['-stdlib=libc++',
                  '--target=x86_64-pc-windows-gnu',
                 ]
        cflags.extend(self.cflags_lists_define())
        cflags.append('-fno-exceptions')
        cflags.append('-fno-rtti')

        cxxflags = list(cflags)
        cxxflags.extend(('-I', os.path.join(windows64_install, 'include', 'c++', 'v1')))

        zlib_path = os.path.abspath(
            os.path.join('../../', 'prebuilts', 'clang', 'host', 'windows-x86', 'toolchain-prebuilts', 'zlib'))
        zlib_inc = os.path.join(zlib_path, 'include')
        zlib_lib = os.path.join(zlib_path, 'lib')

        cflags.extend(['-I', zlib_inc])
        cxxflags.extend(['-I', zlib_inc])
        ldflags.extend(['-L', zlib_lib])


        lldb_mi_env = dict(self.build_config.ORIG_ENV)
        lldb_mi_env['LD_LIBRARY_PATH'] = os.path.join(build_dir, 'lib')
        self.rm_cmake_cache(lldb_mi_windows64_path)

        self.invoke_cmake(lldb_mi_cmake_path,
                          lldb_mi_windows64_path,
                          self.lldb_mi_windefines_dictionary(cc, cxx, windows_sysroot, cflags, cxxflags, ldflags),
                          env=lldb_mi_env)

        self.invoke_ninja(lldb_mi_windows64_path,
                          env=lldb_mi_env,
                          install=False)

        self.llvm_package.copy_lldb_mi_to_windows(windows64_install, lldb_mi_windows64_path)


class LlvmPackage(BuildUtils):

    def __init__(self, build_config):
        super(LlvmPackage, self).__init__(build_config)

    def copy_lldb_server_to_llvm_install(self, lldb_path, crt_install, llvm_triple):
        # Copy lldb-server
        libname = 'lldb-server'
        src_lib = os.path.join(lldb_path, 'bin', libname)
        dst_dir = os.path.join(crt_install, 'bin', llvm_triple)
        self.check_create_dir(dst_dir)
        self.check_copy_file(src_lib, os.path.join(dst_dir, libname))

    def copy_lldb_mi_to_llvm_install(self, lldb_mi_path, llvm_install):
        # Copy lldb-mi
        lldb_mi_dst = os.path.join(llvm_install, 'bin', 'lldb-mi')
        lldb_mi_src = os.path.join(lldb_mi_path, 'src', 'lldb-mi')

        if os.path.exists(lldb_mi_dst):
            os.remove(lldb_mi_dst)
        self.check_copy_file(lldb_mi_src, lldb_mi_dst)

    def copy_lldb_mi_to_windows(self, windows64_install, lldb_mi_windows64_path):
        # Copy lldb-mi for windows
        lldb_mi_dst = os.path.join(windows64_install, 'bin', 'lldb-mi.exe')
        lldb_mi_src = os.path.join(lldb_mi_windows64_path, 'src', 'lldb-mi.exe')
        if os.path.exists(lldb_mi_dst):
            os.remove(lldb_mi_dst)
        self.check_copy_file(lldb_mi_src, lldb_mi_dst)

    def package_libcxx(self):
        libcxx_ndk_install=self.merge_out_path('libcxx-ndk')
        libcxx_ndk_install_include=self.merge_out_path(libcxx_ndk_install, 'include', 'libcxx-ohos', 'include', 'c++', 'v1')
        hosts_list=['linux-x86_64', 'darwin-x86_64', 'windows-x86_64', 'darwin-arm64']

        if os.path.exists(libcxx_ndk_install):
            for headerfile in os.listdir(libcxx_ndk_install_include):
                if not headerfile =='__config':
                    if os.path.isdir(os.path.join(libcxx_ndk_install_include, headerfile)):
                        shutil.rmtree(os.path.join(libcxx_ndk_install_include, headerfile))
                    else:
                        os.remove(os.path.join(libcxx_ndk_install_include, headerfile))

            #Package libcxx-ndk
            for host in hosts_list:
                tarball_name = 'libcxx-ndk-%s-%s' % (self.build_config.build_name, host)
                package_path = '%s%s' % (self.merge_out_path(tarball_name), '.tar.bz2')
                self.logger().info('Packaging %s', package_path)
                args = ['tar', '-chjC', self.build_config.OUT_PATH, '-f', package_path, 'libcxx-ndk']
                self.check_call(args)
            self.check_rm_tree(libcxx_ndk_install)

    @staticmethod
    def merge_tree(src_dir, dst_dir):
        for item in os.listdir(src_dir):
            src_path = os.path.join(src_dir, item)
            dst_path = os.path.join(dst_dir, item)
            if os.path.isfile(src_path):
                shutil.copyfile(src_path, dst_path)
            else:
                shutil.copytree(src_path, dst_path)


    def install_mingw_python(self, install_dir):
        py_root = self.merge_out_path('../third_party', 'mingw-w64', 'mingw-w64-python', 
                                        self.build_config.LLDB_PY_VERSION)
        bin_root = os.path.join(install_dir, 'bin')
        py_dll = 'libpython' + self.build_config.LLDB_PY_VERSION + '.dll'
        shutil.copyfile(os.path.join(py_root, py_dll), os.path.join(bin_root, py_dll))
        self.merge_tree(os.path.join(py_root, 'lib'),
                        os.path.join(bin_root, 'python', 'lib', 'python%s' % self.build_config.LLDB_PY_VERSION))





    def windows_lib_files_operation(self, lib_files, lib_dir, install_dir):

            # Remove unnecessary Windows lib files.
            windows_necessary_lib_files = ['libc++.a', 'libc++abi.a']

            for lib_file in lib_files:
                if lib_file.endswith('.a') and lib_file not in windows_necessary_lib_files:
                    static_library = os.path.join(lib_dir, lib_file)
                    os.remove(static_library)

            for necessary_lib_file in windows_necessary_lib_files:
                if not os.path.isfile(os.path.join(lib_dir, necessary_lib_file)):
                    raise RuntimeError('Did not find %s under %s' % (necessary_lib_file, lib_dir))
            self.install_mingw_python(install_dir)


    def darwin_stripped_xargs(self, bin_dir, necessary_bin_files, script_bins, need_x_bins_darwin):
        for bin_filename in os.listdir(bin_dir):
            binary = os.path.join(bin_dir, bin_filename)
            if not os.path.isfile(binary):
                continue
            if bin_filename not in necessary_bin_files:
                os.remove(binary)
            elif bin_filename not in script_bins:
                if bin_filename not in need_x_bins_darwin and self.host_is_darwin():
                    self.check_call(['strip', '-x', binary])
                else:
                    self.check_call(['strip', binary])


    def strip_lldb_server(self, host, install_dir):
        clang_version_bin_dir = os.path.join(install_dir, 'lib', 'clang', self.build_config.CLANG_VERSION, 'bin')

        if not host.startswith('linux') or not os.path.exists(clang_version_bin_dir):
            return
        llvm_strip = os.path.join(install_dir, 'bin', 'llvm-strip')
        for llvm_triple_dir in os.listdir(clang_version_bin_dir):
            llvm_triple_bin_dir = os.path.join(clang_version_bin_dir, llvm_triple_dir)

            if not os.path.isdir(llvm_triple_bin_dir):
                continue

            for bin_filename in os.listdir(llvm_triple_bin_dir):
                binary = os.path.join(llvm_triple_bin_dir, bin_filename)
                if os.path.isfile(binary):
                    self.check_call([llvm_strip, binary])


    def notice_prebuilts_file(self, host, projects, install_dir):

        # Fetch all the LICENSE.* files under our projects and append them into a
        # Single NOTICE file for the resulting prebuilts.
        notices = []
        for project in projects:
            license_pattern = os.path.abspath(os.path.join(self.build_config.LLVM_PROJECT_DIR, project, 'LICENSE.*'))
            for license_file in glob.glob(license_pattern):
                with open(license_file) as notice_file:
                    notices.append(notice_file.read())

        zlib_license_file = os.path.abspath(os.path.join(self.build_config.REPOROOT_DIR, 'third_party/zlib/LICENSE'))
        with open(zlib_license_file) as notice_file:
            notices.append(notice_file.read())

        if host.startswith('windows'):
            mingw_license_file = os.path.abspath(os.path.join(self.build_config.REPOROOT_DIR, 
                                                                'third_party/mingw-w64/COPYING'))
            with open(mingw_license_file) as notice_file:
                notices.append(notice_file.read())

        with open(os.path.join(install_dir, 'NOTICE'), 'w') as notice_file:
            notice_file.write('\n'.join(notices))


    def tar_windows_mingw(self, host):

        if host.startswith('windows'):
            windows64_install = self.merge_out_path('windows-x86_64-install')
            clang_mingw_dir = self.merge_out_path('clang_mingw')
            clang_mingw_sysroot_dir = os.path.join(clang_mingw_dir, 'clang-%s' % self.build_config.CLANG_VERSION, 'x86_64-w64-mingw32')
            package_name = 'clang-mingw-%s' % self.build_config.build_name
            clang_mingw_sysroot_lib_dir = os.path.join(clang_mingw_sysroot_dir, 'lib')
            clang_windows_lib_dir = os.path.join(windows64_install, 'lib')

            necessary_lib = {'libc++abi.a', 'libc++.a', 'libunwind.a'}
            for libname in necessary_lib:
                shutil.copy2(os.path.join(clang_windows_lib_dir, libname), clang_mingw_sysroot_lib_dir)

            self.check_copy_tree(os.path.join(windows64_install, 'include', 'c++'),
                                 os.path.join(clang_mingw_sysroot_dir, 'include', 'c++'))

            lib_files = []
            if os.path.isdir(os.path.join(clang_mingw_dir, 'clang-%s' % self.build_config.CLANG_VERSION, 'lib')):
                lib_files = os.listdir(os.path.join(clang_mingw_dir, 'clang-%s' % self.build_config.CLANG_VERSION, 'lib'))
            for lib_file in lib_files:
                if lib_file.endswith('.a'):
                    static_library = os.path.join(os.path.join(clang_mingw_dir, 'clang-%s' % self.build_config.CLANG_VERSION, 'lib'), lib_file)
                    os.remove(static_library)

            self.check_rm_tree(os.path.join(clang_mingw_dir, 'clang-%s' % self.build_config.CLANG_VERSION, 'include'))

            package_path = '%s%s' % (self.merge_out_path(package_name), '.tar.bz2')
            self.logger().info('Packaging %s', package_path)
            toolchain_name = 'clang-%s' % self.build_config.build_name
            self.check_copy_tree(os.path.join(clang_mingw_dir, 'clang-%s' % self.build_config.CLANG_VERSION),
                                 os.path.join(clang_mingw_dir, toolchain_name))
            args = ['tar', '-cjC', clang_mingw_dir, '-f', package_path, toolchain_name]
            self.check_call(args)
            self.check_rm_tree(os.path.join(clang_mingw_dir, toolchain_name))

    def package_clang_bin_file(self, necessary_bin_files, ext):
        necessary_bin_file = [
            'clang%s' % ext,
            'clang++%s' % ext,
            'clang-cpp%s' % ext,
            'clang-%s%s' % (self.build_config.VERSION.split('.')[0], ext),
            'clang-check%s' % ext,
            'clang-cl%s' % ext,
            'clang-format%s' % ext,
            'clang-tidy%s' % ext,
            'clangd%s' % ext,
            'count%s' % ext,
            ]
        necessary_bin_files.extend(necessary_bin_file)

    @staticmethod
    def package_lldb_bin_file(necessary_bin_files, ext):
        necessary_bin_file = [
            'dsymutil%s' % ext,
            'FileCheck%s' % ext,
            'git-clang-format',
            'ld.lld%s' % ext,
            'ld64.lld%s' % ext,
            'lld%s' % ext,
            'lld-link%s' % ext,
            'lldb%s' % ext,
            'lldb-argdumper%s' % ext,
            'lldb-server%s' % ext,
            'lldb-vscode%s' % ext,
            ]
        necessary_bin_files.extend(necessary_bin_file)

    @staticmethod
    def package_llvm_bin_file(necessary_bin_files, ext):
        necessary_bin_file = [
            'llvm-addr2line%s' % ext,
            'llvm-ar%s' % ext,
            'llvm-as%s' % ext,
            'llvm-cfi-verify%s' % ext,
            'llvm-config%s' % ext,
            'llvm-cov%s' % ext,
            'llvm-cxxfilt%s' % ext,
            'llvm-dis%s' % ext,
            'llvm-lib%s' % ext,
            'llvm-link%s' % ext,
            'llvm-modextract%s' % ext,
            'llvm-nm%s' % ext,
            'llvm-objcopy%s' % ext,
            'llvm-objdump%s' % ext,
            'llvm-profdata%s' % ext,
            'llvm-ranlib%s' % ext,
            'llvm-readelf%s' % ext,
            'llvm-readobj%s' % ext,
            'llvm-size%s' % ext,
            'llvm-strings%s' % ext,
            'llvm-strip%s' % ext,
            'llvm-symbolizer%s' % ext,
            ]
        necessary_bin_files.extend(necessary_bin_file)

    @staticmethod
    def package_scan_bin_file(necessary_bin_files, ext):
        necessary_bin_file = [
            'not%s' % ext,
            'sancov%s' % ext,
            'sanstats%s' % ext,
            'scan-build%s' % ext,
            'scan-view%s' % ext,
            'yaml2obj%s' % ext,
            ]
        necessary_bin_files.extend(necessary_bin_file)

    @staticmethod
    def package_license_project_tuple():
        # Install license files as NOTICE in the toolchain install dir.
        projects = (
            'llvm',
            'compiler-rt',
            'libcxx',
            'libcxxabi',
            'openmp',
            'clang',
            'clang-tools-extra',
            'lld',
            'lldb',
            'libunwind',
        )
        return projects

    def remove_unnecessary_bin(self, necessary_bin_files, host, lib_dir, install_dir, ext, shlib_ext):

        lib_files = []
        if os.path.isdir(lib_dir):
                lib_files = os.listdir(lib_dir)
        if host.startswith('windows'):
            vers_major = int(self.build_config.VERSION.split('.')[0])
            # Redefining necessary bin files for Windows.
            windows_forbidden_list_bin_files = ['clang-%s%s' % (vers_major, ext), 'scan-build%s' % ext,
                                           'scan-view%s' % ext]
            windows_additional_bin_files = ['liblldb%s' % shlib_ext, 'libpython3.10%s' % shlib_ext,
                                            ('libLLVM%s' if vers_major > 10 else 'LLVM%s') % shlib_ext]

            new_necessary_bin_files = list(set(necessary_bin_files) - set(windows_forbidden_list_bin_files))
            new_necessary_bin_files.extend(windows_additional_bin_files)
            del necessary_bin_files[:]
            necessary_bin_files.extend(new_necessary_bin_files)

            self.windows_lib_files_operation(lib_files, lib_dir, install_dir)
        else:
            # Remove unnecessary static libraries.
            for lib_file in lib_files:
                if lib_file.endswith('.a'):
                    static_library = os.path.join(lib_dir, lib_file)
                    os.remove(static_library)

    def package_up_resulting(self, package_name, host, install_host_dir):
        # Package up the resulting trimmed install/ directory.
        tarball_name = '%s-%s' % (package_name, host)
        package_path = '%s%s' % (self.merge_out_path(tarball_name), '.tar.bz2')
        self.logger().info('Packaging %s', package_path)
        args = ['tar', '-cjC', install_host_dir, '-f', package_path, package_name]
        if host.startswith('windows'):
            # windows do not support symlinks,
            # replace them with file copies
            args.insert(1, '--dereference')

        self.check_call(args)

        # Package ohos NDK
        if os.path.exists(self.merge_out_path('sysroot')):
            tarball_ndk_name = 'ohos-sysroot-%s' % self.build_config.build_name
            package_ndk_path = '%s%s' % (self.merge_out_path(tarball_ndk_name), '.tar.bz2')
            self.logger().info('Packaging %s', package_ndk_path)
            args = ['tar', '-chjC', self.build_config.OUT_PATH, '-f', package_ndk_path, 'sysroot']
            self.check_call(args)

    # Packing Operation.

    def package_operation(self, build_dir, host):

        self.logger().info('Packaging for other environments.')
        package_name = 'clang-%s' % self.build_config.build_name
        self.set_clang_version(build_dir)

        if host.startswith('windows'):
            ext = '.exe'
            shlib_ext = '.dll'
        elif host.startswith('linux'):
            ext = ''
            shlib_ext = '.so'
        else:
            ext = ''
            shlib_ext = '.dylib'

        install_dir = self.merge_out_path('install', host, package_name)
        bin_dir = os.path.join(install_dir, 'bin')
        lib_dir = os.path.join(install_dir, 'lib')
        install_host_dir = os.path.abspath(os.path.join(install_dir, '../'))
        self.check_rm_tree(install_host_dir)
        self.check_copy_tree(build_dir, install_dir)
        # copy readme file to install_dir
        shutil.copyfile(os.path.join(self.build_config.CURRENT_DIR, "toolchain_readme.md"),
         os.path.join(install_dir, "README.md"))

        # Remove unnecessary binaries.
        necessary_bin_files = []
        self.package_clang_bin_file(necessary_bin_files, ext)
        self.package_lldb_bin_file(necessary_bin_files, ext)
        self.package_llvm_bin_file(necessary_bin_files, ext)
        self.package_scan_bin_file(necessary_bin_files, ext)

        if self.build_config.need_lldb_mi:
            necessary_bin_files.append('lldb-mi%s' % ext)
        self.remove_unnecessary_bin(necessary_bin_files, host, lib_dir, install_dir, ext, shlib_ext)

        # Scripts that should not be stripped
        script_bins = ['git-clang-format', 'scan-build', 'scan-view']
        need_x_bins_darwin = ['clang', 'clang++', 'clang-9', 'clang-cl', 'clang-cpp']

        # Bin file in the list should be stripped with -x args when host=darwin
        self.darwin_stripped_xargs(bin_dir, necessary_bin_files, script_bins, need_x_bins_darwin)

        # Strip lldb-server
        self.strip_lldb_server(host, install_dir)

        for necessary_bin_file in necessary_bin_files:
            if not os.path.isfile(os.path.join(bin_dir, necessary_bin_file)):
                print('Did not find %s in %s' % (necessary_bin_file, bin_dir))
                raise RuntimeError('Did not find %s in %s' % (necessary_bin_file, bin_dir))

        cmake_dir = os.path.join(lib_dir, 'cmake')
        self.check_rm_tree(cmake_dir)

        self.notice_prebuilts_file(host, self.package_license_project_tuple(), install_dir)

        create_tar = True
        if create_tar:
            self.package_up_resulting(package_name, host, install_host_dir)

        # Tar windows mingw
        self.tar_windows_mingw(host)
        return



def main():
    build_config = BuildConfig()
    build_utils = BuildUtils(build_config)
    sysroot_composer = SysrootComposer(build_config)
    llvm_core = LlvmCore(build_config)
    llvm_package = LlvmPackage(build_config)
    lldb_mi = LldbMi(build_config, llvm_package)
    llvm_libs = LlvmLibs(build_config, sysroot_composer, llvm_package)

    args = build_config.parse_args()
    need_host = build_utils.host_is_darwin() or ('linux' not in args.no_build)
    need_windows = build_utils.host_is_linux() and \
                   ('windows' not in args.no_build)

    llvm_install = build_utils.merge_out_path('llvm-install')
    windows64_install = build_utils.merge_out_path('windows-x86_64-install')

    configs = []
    if not build_config.no_build_arm:
        configs.append(('arm', build_utils.liteos_triple('arm')))
        configs.append(('arm', build_utils.open_ohos_triple('arm')))

    if not build_config.no_build_aarch64:
        configs.append(('arm64', build_utils.open_ohos_triple('aarch64')))

    if not build_config.no_build_x86_64:
        configs.append(('x86_64', build_utils.open_ohos_triple('x86_64')))


    if build_config.do_build and need_host:
        llvm_core.llvm_compile(
            build_config.build_name,
            llvm_install,
            build_config.debug,
            build_config.no_lto,
            build_config.build_instrumented,
            build_config.xunit_xml_output)
            
    llvm_core.set_clang_version(llvm_install)

    if build_config.do_build and build_utils.host_is_linux():
        sysroot_composer.setup_cmake_platform(llvm_install)
        llvm_libs.build_crt_libs(configs, llvm_install, build_config.need_lldb_server)

        if build_config.need_libs:
            for (arch, target) in configs:
                llvm_libs.build_libs(build_config.need_lldb_server, llvm_install, target)

    if build_config.do_build and need_windows:
        mingw.main()
        llvm_libs.build_need_libs_for_windows()
        llvm_core.llvm_compile_for_windows(build_config.TARGETS,
                                          build_config.enable_assertions,
                                          build_config.build_name)

        if build_config.need_lldb_mi:
            lldb_mi.build_lldb_mi_for_windows()

    if build_config.do_build and build_config.need_lldb_mi and need_host:
        lldb_mi.build_lldb_mi(llvm_install)

    if build_config.do_package:
        if build_utils.host_is_linux():
            llvm_package.package_libcxx()
        llvm_package.package_operation(llvm_install, build_utils.use_platform())
        if build_config.do_package and need_windows:
            llvm_package.package_operation(windows64_install, 'windows-x86_64')

if __name__ == '__main__':
    main()
