from douyin_live_sense.platform_support import analysis_supported


def test_linux_arm64_is_record_only():
    supported, reason = analysis_supported("Linux", "aarch64")
    assert supported is False and "ARM64" in reason


def test_macos_arm64_and_linux_x86_are_supported():
    assert analysis_supported("Darwin", "arm64") == (True, "")
    assert analysis_supported("Linux", "x86_64") == (True, "")
