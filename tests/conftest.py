import pytest
from podcast_studio.model import Project, Speaker, Segment, Word


@pytest.fixture
def project():
    return Project("Synthetic conversation", duration=14500,
        speakers={"a": Speaker("a", "Speaker A"), "b": Speaker("b", "Speaker B")},
        segments=[Segment("s1", "a", 0, 2, "Remote teams collaborate.", "Remote teams collaborate.",
                          [Word("Remote", 0, .5), Word("teams", .5, 1), Word("collaborate.", 1.1, 2)], .95),
                  Segment("s2", "b", 2, 4, "I disagree about noise.", "I disagree about noise.", confidence=.65, overlap=True),
                  Segment("s3", "a", 14400, 14405, "Let us test remote audio.", "Let us test remote audio.", confidence=.9)])
