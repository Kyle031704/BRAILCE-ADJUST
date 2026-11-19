
import sys
sys.path.insert(0, '.')
from brailce.speech.filipino_engine import FilipinoEngine, get_filipino_character_path

# 1. Path check
for char in ['a', 'A', 'b', 'B', 'z', 'Z']:
    p = get_filipino_character_path(char)
    assert p is not None and p.endswith(f'char_{char.upper()}.wav'), f'Failed for {char}: {p}'

# 2. Mock fallback check
class MockEnglishEngine:
    def play_cached_character(self, ch):
        raise RuntimeError(f'WRONG-VOICE FALLBACK TRIGGERED for {ch}')

engine = FilipinoEngine(fallback_speech=type('Fallback', (), {'_character_engine': MockEnglishEngine()})())

# Verify enqueue_character
queued = []
engine.enqueue_wav = lambda p, interrupt=False: queued.append(p)
engine.enqueue_character('a')
engine.enqueue_character('A')
assert len(queued) == 2
assert all(p.endswith('char_A.wav') for p in queued)

print('PASS: Single letters resolve strictly to char_{UPPER}.wav with zero fallback.')
