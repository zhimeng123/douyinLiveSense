import platform


def analysis_supported(system: str | None = None, machine: str | None = None) -> tuple[bool, str]:
    """Return whether local analysis dependencies can be installed on this platform."""
    system = system or platform.system()
    machine = machine or platform.machine()
    if system == "Linux" and machine.lower() in {"aarch64", "arm64"}:
        return False, "Linux ARM64 缺少 torchcodec wheel，仅支持录音。"
    return True, ""
