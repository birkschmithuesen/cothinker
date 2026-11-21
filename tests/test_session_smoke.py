import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

from consumer_harness import REPO, free_port, run_consumer

FINALS = [
    "The harvest festival starts at noon on Saturday.",
    "We need twelve tables and a stage by the old mill.",
    "Mara will bring the lanterns on Friday evening.",
]


def test_consumer_writes_raw_transcript_and_stage(tmp_path):
    run = run_consumer(tmp_path, finals=FINALS)
    assert run.returncode == 0, run.log
    assert not run.timed_out, run.log
    assert run.session_dir is not None, run.log
    for text in FINALS:
        assert text in run.protocol_text
    assert run.stage_json is not None and isinstance(run.stage_json, dict)


def test_stage_serves_the_consumers_stage_file(tmp_path):
    run = run_consumer(tmp_path, finals=FINALS[:1])
    assert run.returncode == 0, run.log
    assert not run.timed_out, run.log
    port = free_port()
    env = {"PATH": "/usr/bin:/bin", "COTHINKER_PROJECT_DIR": str(run.project)}
    errlog = tmp_path / "stage.err"
    with open(errlog, "w") as err:
        proc = subprocess.Popen(
            [sys.executable, str(REPO / "stage" / "stage.py"), "--project",
             "demo", "--no-token", "--host", "127.0.0.1", "--port", str(port)],
            env=env, stdout=subprocess.DEVNULL, stderr=err)
        try:
            url = f"http://127.0.0.1:{port}/parts"
            body = None
            for _ in range(50):
                assert proc.poll() is None, errlog.read_text()
                try:
                    with urllib.request.urlopen(url, timeout=5) as r:
                        assert r.status == 200
                        body = json.loads(r.read().decode("utf-8"))
                    break
                except urllib.error.HTTPError:
                    raise
                except OSError:
                    time.sleep(0.2)
            assert body is not None, errlog.read_text()
            mirror = (run.stage_json or {}).get("mirror")
            assert mirror, run.stage_json
            assert mirror in json.dumps(body, ensure_ascii=False), body
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
