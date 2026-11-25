from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Sample cues from the SCTE 35 specification's examples.
TIME_SIGNAL_PPO = "/DA0AAAAAAAA///wBQb+cr0AUAAeAhxDVUVJSAAAjn/PAAGlmbAICAAAAAAsoKGKNAIAmsnRfg=="
SPLICE_INSERT_OUT = "/DAvAAAAAAAA///wFAVIAACPf+/+c2nALv4AUsz1AAAAAAAKAAhDVUVJAAABNWLbowo="


@pytest.fixture
def fixture_text():
    def _read(name: str) -> str:
        return (FIXTURES / name).read_text()

    return _read
