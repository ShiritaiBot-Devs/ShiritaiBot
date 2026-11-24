import pytest

from shiritaibot.i18n import DEFAULT_LOCALE, set_instance_locale


@pytest.fixture(autouse=True)
def instance_locale():
    """Loading a config sets the process-wide bot language; keep tests independent."""
    set_instance_locale(DEFAULT_LOCALE)
    yield
    set_instance_locale(DEFAULT_LOCALE)
