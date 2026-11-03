"""`so101 demo`: practice driving a session (no hardware, nothing moves).

Asks the same kinds of questions real tasks ask, so a new operator or AI can learn the
protocol: read the prompt, decide or ask the human, answer; 'stop' aborts.
"""
from __future__ import annotations

import time


def main(a, prof, sess):
    sess.event("demo_step", step=1, note="a real task would move the arm here")
    sess.ask("DEMO: pretend the gripper is open. Put the (imaginary) cube under the jaws, then 'go'", ["go", "stop"])
    for i in range(3):
        sess.check_stop()
        time.sleep(0.3)
        sess.event("demo_progress", i=i + 1, of=3)
    c = sess.ask("DEMO: how high is the pen tip above the paper, in mm? (any number)")
    try:
        sess.event("demo_number", mm=float(c))
    except ValueError:
        sess.say("that was not a number - real tasks re-ask in that case")
    sess.ask("DEMO: finished. 'go' to end", ["go"])
    sess.event("demo_done")
