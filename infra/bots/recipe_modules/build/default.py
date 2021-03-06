# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


from . import util


def build_command_buffer(api, chrome_dir, skia_dir, out):
  api.run(api.python, 'build command_buffer',
      script=skia_dir.join('tools', 'build_command_buffer.py'),
      args=[
        '--chrome-dir', chrome_dir,
        '--output-dir', out,
        '--no-sync', '--no-hooks', '--make-output-dir'])


def compile_swiftshader(api, swiftshader_root, cc, cxx, out):
  """Build SwiftShader with CMake.

  Building SwiftShader works differently from any other Skia third_party lib.
  See discussion in skia:7671 for more detail.

  Args:
    swiftshader_root: root of the SwiftShader checkout.
    cc, cxx: compiler binaries to use
    out: target directory for libEGL.so and libGLESv2.so
  """
  cmake_bin = str(api.vars.slave_dir.join('cmake_linux', 'bin'))
  env = {
      'CC': cc,
      'CXX': cxx,
      'PATH': '%%(PATH)s:%s' % cmake_bin
  }
  api.file.ensure_directory('makedirs swiftshader_out', out)
  with api.context(cwd=out, env=env):
    api.run(api.step, 'swiftshader cmake',
            cmd=['cmake', swiftshader_root, '-GNinja'])
    api.run(api.step, 'swiftshader ninja',
            cmd=['ninja', '-C', out, 'libEGL.so', 'libGLESv2.so'])


def compile_fn(api, checkout_root, out_dir):
  skia_dir      = checkout_root.join('skia')
  compiler      = api.vars.builder_cfg.get('compiler',      '')
  configuration = api.vars.builder_cfg.get('configuration', '')
  extra_tokens  = api.vars.extra_tokens
  os            = api.vars.builder_cfg.get('os',            '')
  target_arch   = api.vars.builder_cfg.get('target_arch',   '')

  clang_linux        = str(api.vars.slave_dir.join('clang_linux'))
  emscripten_sdk     = str(api.vars.slave_dir.join('emscripten_sdk'))
  linux_vulkan_sdk   = str(api.vars.slave_dir.join('linux_vulkan_sdk'))
  win_toolchain = str(api.vars.slave_dir.join(
    't', 'depot_tools', 'win_toolchain', 'vs_files',
    'a9e1098bba66d2acccc377d5ee81265910f29272'))
  win_vulkan_sdk = str(api.vars.slave_dir.join('win_vulkan_sdk'))
  moltenvk = str(api.vars.slave_dir.join('moltenvk'))

  cc, cxx = None, None
  extra_cflags = []
  extra_ldflags = []
  args = {}
  env = {}

  if compiler == 'Clang' and api.vars.is_linux:
    cc  = clang_linux + '/bin/clang'
    cxx = clang_linux + '/bin/clang++'
    extra_cflags .append('-B%s/bin' % clang_linux)
    extra_ldflags.append('-B%s/bin' % clang_linux)
    extra_ldflags.append('-fuse-ld=lld')
    extra_cflags.append('-DDUMMY_clang_linux_version=%s' %
                        api.run.asset_version('clang_linux', skia_dir))
    if os == 'Ubuntu14':
      extra_ldflags.extend(['-static-libstdc++', '-static-libgcc'])

  elif compiler == 'Clang':
    cc, cxx = 'clang', 'clang++'
  elif compiler == 'GCC':
    if target_arch in ['mips64el', 'loongson3a']:
      mips64el_toolchain_linux = str(api.vars.slave_dir.join(
          'mips64el_toolchain_linux'))
      cc  = mips64el_toolchain_linux + '/bin/mips64el-linux-gnuabi64-gcc-7'
      cxx = mips64el_toolchain_linux + '/bin/mips64el-linux-gnuabi64-g++-7'
      env['LD_LIBRARY_PATH'] = (
          mips64el_toolchain_linux + '/lib/x86_64-linux-gnu/')
      extra_ldflags.append('-L' + mips64el_toolchain_linux +
                           '/mips64el-linux-gnuabi64/lib')
      extra_cflags.extend([
          '-Wno-format-truncation',
          '-Wno-uninitialized',
          ('-DDUMMY_mips64el_toolchain_linux_version=%s' %
           api.run.asset_version('mips64el_toolchain_linux', skia_dir))
      ])
      if configuration == 'Release':
        # This warning is only triggered when fuzz_canvas is inlined.
        extra_cflags.append('-Wno-strict-overflow')
      args.update({
        'skia_use_system_freetype2': 'false',
        'skia_use_fontconfig':       'false',
        'skia_enable_gpu':           'false',
      })
    else:
      cc, cxx = 'gcc', 'g++'
  elif compiler == 'EMCC':
    cc   = emscripten_sdk + '/emscripten/incoming/emcc'
    cxx  = emscripten_sdk + '/emscripten/incoming/em++'
    extra_cflags.append('-Wno-unknown-warning-option')
    extra_cflags.append('-DDUMMY_emscripten_sdk_version=%s' %
                        api.run.asset_version('emscripten_sdk', skia_dir))
  if 'Coverage' in extra_tokens:
    # See https://clang.llvm.org/docs/SourceBasedCodeCoverage.html for
    # more info on using llvm to gather coverage information.
    extra_cflags.append('-fprofile-instr-generate')
    extra_cflags.append('-fcoverage-mapping')
    extra_ldflags.append('-fprofile-instr-generate')
    extra_ldflags.append('-fcoverage-mapping')

  if compiler != 'MSVC' and configuration == 'Debug':
    extra_cflags.append('-O1')

  if 'Exceptions' in extra_tokens:
    extra_cflags.append('/EHsc')
  if 'Fast' in extra_tokens:
    extra_cflags.extend(['-march=native', '-fomit-frame-pointer', '-O3',
                         '-ffp-contract=off'])

  if len(extra_tokens) == 1 and extra_tokens[0].startswith('SK'):
    extra_cflags.append('-D' + extra_tokens[0])
    # If we're limiting Skia at all, drop skcms to portable code.
    if 'SK_CPU_LIMIT' in extra_tokens[0]:
      extra_cflags.append('-DSKCMS_PORTABLE')


  if 'MSAN' in extra_tokens:
    extra_ldflags.append('-L' + clang_linux + '/msan')

  if configuration != 'Debug':
    args['is_debug'] = 'false'
  if 'ANGLE' in extra_tokens:
    args['skia_use_angle'] = 'true'
  if 'SwiftShader' in extra_tokens:
    swiftshader_root = skia_dir.join('third_party', 'externals', 'swiftshader')
    swiftshader_out = out_dir.join('swiftshader_out')
    compile_swiftshader(api, swiftshader_root, cc, cxx, swiftshader_out)
    args['skia_use_egl'] = 'true'
    extra_cflags.extend([
        '-DGR_EGL_TRY_GLES3_THEN_GLES2',
        '-I%s' % skia_dir.join(
            'third_party', 'externals', 'egl-registry', 'api'),
        '-I%s' % skia_dir.join(
            'third_party', 'externals', 'opengl-registry', 'api'),
    ])
    extra_ldflags.extend([
        '-L%s' % swiftshader_out,
    ])
  if 'CommandBuffer' in extra_tokens:
    chrome_dir = checkout_root
    api.run.run_once(build_command_buffer, api, chrome_dir, skia_dir, out_dir)
  if 'MSAN' in extra_tokens:
    args['skia_use_fontconfig'] = 'false'
  if 'ASAN' in extra_tokens or 'UBSAN' in extra_tokens:
    args['skia_enable_spirv_validation'] = 'false'
  if 'Mini' in extra_tokens:
    args.update({
      'is_component_build':     'true',   # Proves we can link a coherent .so.
      'is_official_build':      'true',   # No debug symbols, no tools.
      'skia_enable_effects':    'false',
      'skia_enable_gpu':        'true',
      'skia_enable_pdf':        'false',
      'skia_use_expat':         'false',
      'skia_use_libjpeg_turbo': 'false',
      'skia_use_libpng':        'false',
      'skia_use_libwebp':       'false',
      'skia_use_zlib':          'false',
    })
  if 'NoDEPS' in extra_tokens:
    args.update({
      'is_official_build':         'true',
      'skia_enable_fontmgr_empty': 'true',
      'skia_enable_gpu':           'true',

      'skia_enable_effects':    'false',
      'skia_enable_pdf':        'false',
      'skia_use_expat':         'false',
      'skia_use_freetype':      'false',
      'skia_use_libjpeg_turbo': 'false',
      'skia_use_libpng':        'false',
      'skia_use_libwebp':       'false',
      'skia_use_vulkan':        'false',
      'skia_use_zlib':          'false',
    })
  if 'NoGPU' in extra_tokens:
    args['skia_enable_gpu'] = 'false'
  if 'Shared' in extra_tokens:
    args['is_component_build'] = 'true'
  if 'Vulkan' in extra_tokens and not 'Android' in extra_tokens:
    args['skia_enable_vulkan_debug_layers'] = 'false'
    if api.vars.is_linux:
      args['skia_vulkan_sdk'] = '"%s"' % linux_vulkan_sdk
    if 'Win' in os:
      args['skia_vulkan_sdk'] = '"%s"' % win_vulkan_sdk
    if 'MoltenVK' in extra_tokens:
      args['skia_moltenvk_path'] = '"%s"' % moltenvk
  if 'Metal' in extra_tokens:
    args['skia_use_metal'] = 'true'
  if 'iOS' in extra_tokens:
    # Bots use Chromium signing cert.
    args['skia_ios_identity'] = '".*GS9WA.*"'
    args['skia_ios_profile'] = '"Upstream Testing Provisioning Profile"'
  if 'CheckGeneratedFiles' in extra_tokens:
    args['skia_compile_processors'] = 'true'
    args['skia_generate_workarounds'] = 'true'
  if compiler == 'Clang' and 'Win' in os:
    args['clang_win'] = '"%s"' % api.vars.slave_dir.join('clang_win')
    extra_cflags.append('-DDUMMY_clang_win_version=%s' %
                        api.run.asset_version('clang_win', skia_dir))
  if target_arch == 'wasm':
    args.update({
      'skia_use_freetype':   'false',
      'skia_use_fontconfig': 'false',
      'skia_use_dng_sdk':    'false',
      'skia_use_icu':        'false',
      'skia_enable_gpu':     'false',
    })

  sanitize = ''
  for t in extra_tokens:
    if t.endswith('SAN'):
      sanitize = t
  if 'SafeStack' in extra_tokens:
    assert sanitize == ''
    sanitize = 'safe-stack'

  for (k,v) in {
    'cc':  cc,
    'cxx': cxx,
    'sanitize': sanitize,
    'target_cpu': target_arch,
    'target_os': 'ios' if 'iOS' in extra_tokens else '',
    'win_sdk': win_toolchain + '/win_sdk' if 'Win' in os else '',
    'win_vc': win_toolchain + '/VC' if 'Win' in os else '',
  }.iteritems():
    if v:
      args[k] = '"%s"' % v
  if extra_cflags:
    args['extra_cflags'] = repr(extra_cflags).replace("'", '"')
  if extra_ldflags:
    args['extra_ldflags'] = repr(extra_ldflags).replace("'", '"')

  gn_args = ' '.join('%s=%s' % (k,v) for (k,v) in sorted(args.iteritems()))

  gn    = 'gn.exe'    if 'Win' in os else 'gn'
  ninja = 'ninja.exe' if 'Win' in os else 'ninja'
  gn = skia_dir.join('bin', gn)

  with api.context(cwd=skia_dir):
    api.run(api.python,
            'fetch-gn',
            script=skia_dir.join('bin', 'fetch-gn'),
            infra_step=True)
    if 'CheckGeneratedFiles' in extra_tokens:
      env['PATH'] = '%s:%%(PATH)s' % skia_dir.join('bin')
      api.run(api.python,
              'fetch-clang-format',
              script=skia_dir.join('bin', 'fetch-clang-format'),
              infra_step=True)
    if target_arch == 'wasm':
      fastcomp = emscripten_sdk + '/clang/fastcomp/build_incoming_64/bin'
      env['PATH'] = '%s:%%(PATH)s' % fastcomp

    with api.env(env):
      api.run(api.step, 'gn gen',
              cmd=[gn, 'gen', out_dir, '--args=' + gn_args])
      api.run(api.step, 'ninja', cmd=[ninja, '-k', '0', '-C', out_dir])


def copy_extra_build_products(api, src, dst):
  if 'SwiftShader' in api.vars.extra_tokens:
    util.copy_whitelisted_build_products(api,
        src.join('swiftshader_out'),
        api.vars.swarming_out_dir.join('swiftshader_out'))
