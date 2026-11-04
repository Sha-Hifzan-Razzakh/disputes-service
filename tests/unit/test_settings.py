import pytest
from pydantic import SecretStr, ValidationError

from app.bootstrap.settings import Settings


def test_dev_defaults_are_valid() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.run_timeout_s == 30 and s.recursion_limit == 40 and s.checkpoint_auto_setup


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"env": "prod", "checkpoint_auto_setup": False, "jwt_secret": SecretStr("dev-only-x")}, "APP_JWT_SECRET"),
        ({"env": "prod", "jwt_secret": SecretStr("x" * 40)}, "APP_CHECKPOINT_AUTO_SETUP"),
        ({"model_provider": "anthropic"}, "APP_ANTHROPIC_API_KEY"),
        ({"run_timeout_s": 0}, "greater than 0"),
    ],
)
def test_invalid_combinations_fail_fast(overrides: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **overrides)  # type: ignore[call-arg]
