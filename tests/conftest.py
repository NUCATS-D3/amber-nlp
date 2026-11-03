import os
from datetime import timedelta

from hypothesis import HealthCheck, settings

pytest_plugins = ["pytester"]

# Hypothesis profiles (amber-rst). CI derandomizes so a failure reproduces from the same
# inputs and prints the blob to replay it; "thorough" searches deeper on demand:
# HYPOTHESIS_PROFILE=thorough uv run pytest tests/test_properties.py
settings.register_profile(
    "dev", max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.register_profile(
    "ci",
    max_examples=100,
    derandomize=True,
    deadline=timedelta(seconds=5),
    print_blob=True,
    database=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "thorough", max_examples=2000, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.load_profile(
    os.environ.get("HYPOTHESIS_PROFILE") or ("ci" if os.environ.get("CI") else "dev")
)
