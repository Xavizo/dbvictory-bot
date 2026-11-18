import pytest
from client_profiles import LEGACY, UPDATED, select_profile


@pytest.mark.parametrize("profile", [LEGACY, UPDATED])
def test_exact_digest_selects_reviewed_layout(profile):
    assert select_profile(profile.sha256) is profile


@pytest.mark.parametrize("digest", ["", "0" * 64, UPDATED.sha256.upper(), UPDATED.sha256[:-1]])
def test_unreviewed_build_is_rejected(digest):
    with pytest.raises(ValueError, match="weryfikacja"):
        select_profile(digest)
