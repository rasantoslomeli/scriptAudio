"""Create deterministic fictional project and audio cues; no real people or recordings."""
from pathlib import Path
import math
import struct
import sys
import wave
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from podcast_studio.model import Project, Speaker, Segment, Summary, Claim, Bookmark

root = Path(__file__).resolve().parents[1] / "samples"
root.mkdir(exist_ok=True)
conversations = [
    ("a", 0, 7, "Welcome to our fictional production roundtable. How do small teams work remotely?"),
    ("b", 8, 16, "We share an agenda before recording and keep a separate local audio track for each participant."),
    ("c", 17, 24, "I agree about local tracks. I disagree that every discussion needs a strict agenda."),
    ("a", 25, 31, "That is a useful distinction. What happens when two guests interrupt each other?"),
    ("b", 30, 39, "Separate tracks help us review cross-talk, but automatic speaker labels still need human checking."),
    ("c", 40, 48, "Let us test that next week. We should not describe our pilot as a proven improvement yet."),
    ("a", 49, 57, "Our decision is to run a pilot and compare the results. No guaranteed accuracy claims."),
]
project = Project("Fictional production roundtable", "sample-cues.wav", duration=60,
    media={"codec_name": "pcm_s16le", "sample_rate": "16000", "channels": 1,
           "notice": "Synthetic tone cues, NOT speech. Transcript is fictional, not ASR output."},
    speakers={"a": Speaker("a", "Alex (fictional)", "#56702A", "Host"),
              "b": Speaker("b", "Jordan (fictional)", "#7365A0", "Guest"),
              "c": Speaker("c", "Casey (fictional)", "#28787D", "Guest")},
    segments=[Segment(f"demo_{i+1}", who, start, end, text, text, confidence=.95,
                      overlap=i in (3, 4), flags=["Synthetic demonstration data; no acoustic confidence measured"])
              for i, (who,start,end,text) in enumerate(conversations)],
    bookmarks=[Bookmark(30, "Cross-talk example"), Bookmark(49, "Pilot decision")])
project.summaries = [Summary("demo_summary", "Entire podcast", "Source-linked example (hand authored)", [
    Claim("Jordan describes separate local audio tracks as a way to review interruptions.", ["demo_2", "demo_5"]),
    Claim("Casey disagrees that every discussion needs a strict agenda.", ["demo_3"]),
    Claim("The group plans a pilot; Casey says its benefits are not yet proven.", ["demo_6", "demo_7"]),
], "Hand-authored synthetic fixture, not AI output")]
rate = 16000
with wave.open(str(root / "sample-cues.wav"), "wb") as output:
    output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
    for second in range(60):
        frames = bytearray()
        for i in range(rate):
            t = second + i / rate
            value = sum(math.sin(2*math.pi*frequency*t)*1400 for who,start,end,_ in conversations
                        if start <= t < end for frequency in [{"a": 330, "b": 440, "c": 550}[who]])
            frames.extend(struct.pack("<h", round(value)))
        output.writeframes(frames)
project.file_size = (root / "sample-cues.wav").stat().st_size
project.save(root / "roundtable.pss.json")
print("Wrote samples/roundtable.pss.json and sample-cues.wav (fictional text and tone cues)")
