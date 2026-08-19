"""Shared Bazel command configuration."""

import os
from pathlib import Path


def command() -> list[str]:
    # rules_python resolves C toolchain targets during analysis. These wrappers
    # provide that toolchain without adding a system compiler to the test image.
    compiler = Path(__file__).parent.parent / "tools/cc.py"
    archiver = Path(__file__).parent.parent / "tools/ar.py"
    args = [
        "bazelisk",
        "test",
        f"--repo_env=CC={compiler}",
        f"--repo_env=AR={archiver}",
        "--@rules_python//python/config_settings:bootstrap_impl=script",
    ]
    api_key = os.environ.get("BUILDBUDDY_API_KEY")
    if api_key:
        # Keep the credential in the Bazel client. Remote test actions do not
        # receive this header as an environment variable.
        args.extend(
            [
                "--remote_executor=grpcs://remote.buildbuddy.io",
                "--remote_cache=grpcs://remote.buildbuddy.io",
                f"--remote_header=x-buildbuddy-api-key={api_key}",
                "--remote_download_minimal",
            ]
        )
    return args
