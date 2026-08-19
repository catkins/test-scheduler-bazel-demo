"""Shared Bazel command configuration."""

import os


def command() -> list[str]:
    args = ["bazelisk", "test"]
    api_key = os.environ.get("BUILDBUDDY_API_KEY")
    if api_key:
        args.extend(
            [
                "--remote_executor=grpcs://remote.buildbuddy.io",
                "--remote_cache=grpcs://remote.buildbuddy.io",
                f"--remote_header=x-buildbuddy-api-key={api_key}",
                "--remote_download_minimal",
            ]
        )
    return args
