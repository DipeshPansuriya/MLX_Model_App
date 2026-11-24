"""Pure helpers: version comparison, size formatting, fit rating, pid probes."""

from __future__ import annotations

import pytest

GB = 1024**3


class TestVt:
    def test_parses_three_part_version(self, app_module):
        assert app_module.vt("2.0.10") == (2, 0, 10)

    def test_parses_two_part_version(self, app_module):
        assert app_module.vt("2.1") == (2, 1)

    def test_prerelease_suffix_becomes_zero(self, app_module):
        # "rc1" is not a digit, so it contributes 0.
        assert app_module.vt("2.0.1-rc1") == (2, 0, 1, 0)

    def test_release_is_not_newer_than_its_own_prerelease(self, app_module):
        # Known limitation, recorded rather than fixed: (2,0,1) is a shorter
        # tuple than (2,0,1,0), so Python orders it lower and a final release is
        # not reported as newer than its own rc. Only affects prerelease tags,
        # which this project does not publish.
        assert app_module.is_newer("2.0.1", "2.0.1-rc1") is False

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_returns_empty_tuple(self, app_module, value):
        assert app_module.vt(value) == ()

    def test_non_numeric_segment_becomes_zero(self, app_module):
        assert app_module.vt("2.x.3") == (2, 0, 3)


class TestIsNewer:
    @pytest.mark.parametrize(
        "candidate,current,expected",
        [
            ("2.0.1", "2.0.0", True),
            ("2.0.10", "2.0.9", True),
            ("2.1.0", "2.0.99", True),
            ("3.0.0", "2.99.99", True),
            ("2.0.1", "2.0.1", False),
            ("2.0.0", "2.0.1", False),
            ("1.9.9", "2.0.0", False),
        ],
    )
    def test_ordering(self, app_module, candidate, current, expected):
        assert app_module.is_newer(candidate, current) is expected

    def test_missing_candidate_is_not_newer(self, app_module):
        assert app_module.is_newer(None, "2.0.0") is False

    def test_missing_current_means_newer(self, app_module):
        assert app_module.is_newer("2.0.0", None) is True

    def test_ten_is_greater_than_nine(self, app_module):
        # Guards the string-compare bug: "2.0.10" < "2.0.9" lexicographically.
        assert app_module.is_newer("2.0.10", "2.0.9") is True
        assert app_module.is_newer("2.0.9", "2.0.10") is False


class TestHumanSize:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, "0 B"),
            (512, "512 B"),
            (1024, "1.0 KB"),
            (1536, "1.5 KB"),
            (1024**2, "1.0 MB"),
            (4.3 * GB, "4.3 GB"),
            (1024**4, "1.0 TB"),
        ],
    )
    def test_formatting(self, app_module, value, expected):
        assert app_module.human_size(value) == expected

    def test_never_exceeds_tb(self, app_module):
        assert app_module.human_size(1024**5).endswith("TB")


class TestFitRating:
    RAM_32 = 32 * GB
    RAM_16 = 16 * GB
    RAM_64 = 64 * GB

    def test_small_model_on_32gb_is_great(self, app_module):
        assert app_module.fit_rating(4.3, self.RAM_32) == "great"

    def test_medium_model_on_32gb_is_good(self, app_module):
        assert app_module.fit_rating(8.32, self.RAM_32) == "good"

    def test_large_model_on_32gb_is_tight(self, app_module):
        assert app_module.fit_rating(18.45, self.RAM_32) == "tight"

    def test_huge_model_on_32gb_is_too_large(self, app_module):
        assert app_module.fit_rating(39.71, self.RAM_32) == "too-large"

    def test_same_model_is_tighter_on_16gb(self, app_module):
        assert app_module.fit_rating(8.32, self.RAM_16) == "tight"

    def test_same_model_is_great_on_64gb(self, app_module):
        assert app_module.fit_rating(8.32, self.RAM_64) == "great"

    def test_unknown_ram(self, app_module):
        assert app_module.fit_rating(1.0, 0) == "unknown"

    def test_bands_are_ordered(self, app_module):
        # Monotonic: a bigger model can never be rated better.
        order = ["great", "good", "tight", "too-large"]
        sizes = [1, 4, 8, 14, 18, 22, 30, 40, 80]
        ranks = [order.index(app_module.fit_rating(s, self.RAM_32)) for s in sizes]
        assert ranks == sorted(ranks)


class TestPidAlive:
    def test_none_is_dead(self, app_module):
        assert app_module._pid_alive(None) is False

    @pytest.mark.parametrize("pid", [0, -1])
    def test_non_positive_is_dead(self, app_module, pid):
        assert app_module._pid_alive(pid) is False

    def test_own_pid_is_alive(self, app_module):
        import os

        assert app_module._pid_alive(os.getpid()) is True

    def test_unused_pid_is_dead(self, app_module):
        # 2**22 is above the default pid_max on macOS; os.kill raises ESRCH.
        assert app_module._pid_alive(4194303) is False
