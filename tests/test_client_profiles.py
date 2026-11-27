import pytest
from dbv.client_bridge.client_profiles import LEGACY, NOVEMBER_26, UPDATED, select_profile


@pytest.mark.parametrize("profile", [UPDATED, NOVEMBER_26])
def test_exact_digest_selects_reviewed_layout(profile):
    assert select_profile(profile.sha256) is profile


@pytest.mark.parametrize("digest", [LEGACY.sha256, "", "0" * 64, UPDATED.sha256.upper(), UPDATED.sha256[:-1]])
def test_unreviewed_build_is_rejected(digest):
    with pytest.raises(ValueError, match="weryfikacja"):
        select_profile(digest)
