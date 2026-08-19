#!/usr/bin/env python3
"""Expose Zig's C compiler to Bazel's otherwise-unused C++ toolchain check."""

import os
import sys


os.execvp("zig", ["zig", "cc", *sys.argv[1:]])
