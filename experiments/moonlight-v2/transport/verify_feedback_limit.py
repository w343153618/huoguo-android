#!/usr/bin/env python3
"""All feedback is lost: requests must stop after the finite attempt budget."""
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
binary = Path(tempfile.gettempdir())/'huoguo-udp-native-build/udp_media_probe'
completed = subprocess.run([str(binary), '--loopback', '3', '40000000', '120', '2', '2',
                            '1', '100', '20261001'], capture_output=True, text=True, check=True, timeout=20)
result = json.loads(completed.stdout)
if not (result['idr_requests'] > 0 and result['feedback_packets_sent'] == 0 and
        result['feedback_requests_received'] == 0 and result['forced_idrs'] == 0 and
        0 < result['feedback_packets_lost'] <= 3*result['idr_requests'] and
        result['pending_frames_end'] == 0):
    raise SystemExit('Finite feedback retry/fallback validation failed')
result['finite_feedback_retry_check'] = 'PASS'
output = ROOT/'evidence/feedback-all-lost-20261001.json'
output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(result, ensure_ascii=False))
