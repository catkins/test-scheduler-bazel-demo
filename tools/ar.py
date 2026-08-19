#!/usr/bin/env python3
"""Expose Zig's archiver to Bazel's otherwise-unused C++ toolchain check."""

import os
import sys


os.execvp("zig", ["zig", "ar", *sys.argv[1:]])
