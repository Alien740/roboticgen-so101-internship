"""
SO-101 gamepad control - velocity mode with gravity compensation.
Speed in deg/sec, stops on release, tuned PID for holding under load.
"""
import time

from lerobot.robots.so_follower import SOFollower, SOFollowerRobotConfig
import pygame
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pygame")

# --- SPEED (degrees per second) ---
NORMAL_SPEED = 40.0
BOOST_SPEED = 100.0

# --- PORTS ---
PORT = "COM4"
ROBOT_ID = "my_so101"

# --- LOOP / INPUT ---
LOOP_SLEEP = 0.02
DEADZONE = 0.15
TRIGGER_ON = 0.5
TRIGGER_OFF = 0.3
MAX_RELATIVE = 15.0
HOLD_TORQUE = 1000
DEADBAND_SHOULDER = 2.0

# --- PID GAINS (STS3215 motor registers) ---
P_GAIN = 40
I_GAIN = 8
D_GAIN = 48

# --- AXES / BUTTONS ---
AX_LEFT_X, AX_LEFT_Y = 0, 1
AX_RIGHT_X, AX_RIGHT_Y = 2, 3
AX_LT, AX_RT = 4, 5
BTN_A, BTN_B, BTN_X, BTN_Y = 0, 1, 2, 3
BTN_LB, BTN_RB = 4, 5

# --- POSES / LIMITS ---
HOME = {
    "shoulder_pan.pos": 0.0,
    "shoulder_lift.pos": -30.0,
    "elbow_flex.pos": 30.0,
    "wrist_flex.pos": 20.0,
    "wrist_roll.pos": 0.0,
    "gripper.pos": 50.0,
}

JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex",
          "wrist_flex", "wrist_roll", "gripper"]

LIMITS = {
    "shoulder_pan": (-100.0, 100.0),
    "shoulder_lift": (-100.0, 100.0),
    "elbow_flex": (-100.0, 100.0),
    "wrist_flex": (-90.0, 90.0),
    "wrist_roll": (-180.0, 180.0),
    "gripper": (0.0, 100.0),
}


def clamp(name, value):
    lo, hi = LIMITS.get(name, (-180.0, 180.0))
    return max(lo, min(hi, value))


def dead(v):
    return 0.0 if abs(v) < DEADZONE else v


def boost_max_torque(robot):
    print("Boosting motor torque...")
    for j in JOINTS:
        try:
            robot.bus.write("Max_Torque_Limit", j, HOLD_TORQUE, normalize=False)
        except Exception as e:
            print(f"  {j}: {e}")


def boost_heavy_joints(robot):
    """Give shoulder and elbow motors maximum torque authority."""
    heavy = ["shoulder_lift", "elbow_flex", "shoulder_pan"]
    for j in heavy:
        try:
            robot.bus.write("Max_Torque_Limit", j, 1000, normalize=False)
            print(f"  {j}: torque limit = 1000")
        except Exception as e:
            print(f"  {j}: {e}")


def print_positions(targets, actual):
    print("\n--- Positions ---")
    print(f"{'Joint':<16}{'Target':>9}{'Actual':>9}{'Drift':>9}")
    for j in JOINTS:
        k = f"{j}.pos"
        t = targets.get(k, 0.0)
        a = actual.get(k, 0.0)
        flag = "  DRIFT" if abs(a - t) > 3.0 else ""
        print(f"{j:<16}{t:9.2f}{a:9.2f}{a - t:9.2f}{flag}")


def main():
    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        print("No gamepad.")
        return

    js = pygame.joystick.Joystick(0)
    js.init()

    config = SOFollowerRobotConfig(
        id=ROBOT_ID,
        port=PORT,
        use_degrees=True,
        disable_torque_on_disconnect=True,
        max_relative_target=MAX_RELATIVE,
        position_p_coefficient=P_GAIN,
        position_i_coefficient=I_GAIN,
        position_d_coefficient=D_GAIN,
    )

    with SOFollower(config) as robot:
        print(f"Connected to {ROBOT_ID} on {PORT}")
        boost_max_torque(robot)
        boost_heavy_joints(robot)

        # Initialize targets to actual pose
        obs = robot.get_observation()
        targets = {k: float(v) for k, v in obs.items() if k.endswith(".pos")}

        # Trigger hysteresis state
        lt_active = False
        rt_active = False

        print(f"""
--- Controls (speeds in deg/sec) ---
Left Stick   : Shoulder Pan / Lift
Right Stick  : Wrist Roll / Flex
LB / RB      : Elbow - / +
LT / RT      : Gripper close / open
Y (hold)     : Speed boost ({BOOST_SPEED} deg/s)
A            : Home | B : Print | X : Quit

Normal speed: {NORMAL_SPEED} deg/s
""")

        prev = [0] * js.get_numbuttons()
        last_time = time.perf_counter()

        while True:
            pygame.event.pump()

            # --- Time step ---
            now = time.perf_counter()
            dt = now - last_time
            last_time = now
            if dt > 0.1:
                dt = 0.1

            # --- Read actual once ---
            obs = robot.get_observation()
            actual = {k: v for k, v in obs.items() if k.endswith(".pos")}

            # --- Speed ---
            boosting = js.get_button(BTN_Y)
            speed = BOOST_SPEED if boosting else NORMAL_SPEED
            max_step = speed * dt

            # --- Gather inputs ---
            inputs = {}

            lx = dead(js.get_axis(AX_LEFT_X))
            if lx:
                inputs["shoulder_pan"] = lx

            ly = dead(js.get_axis(AX_LEFT_Y))
            if ly:
                inputs["shoulder_lift"] = -ly

            rx = dead(js.get_axis(AX_RIGHT_X))
            if rx:
                inputs["wrist_roll"] = rx

            ry = dead(js.get_axis(AX_RIGHT_Y))
            if ry:
                inputs["wrist_flex"] = -ry

            if js.get_button(BTN_LB):
                inputs["elbow_flex"] = -1.0
            if js.get_button(BTN_RB):
                inputs["elbow_flex"] = 1.0

            # Triggers with hysteresis
            lt_val = js.get_axis(AX_LT)
            rt_val = js.get_axis(AX_RT)
            if not lt_active and lt_val > TRIGGER_ON:
                lt_active = True
            elif lt_active and lt_val < TRIGGER_OFF:
                lt_active = False
            if not rt_active and rt_val > TRIGGER_ON:
                rt_active = True
            elif rt_active and rt_val < TRIGGER_OFF:
                rt_active = False

            if lt_active:
                inputs["gripper"] = -1.0
            if rt_active:
                inputs["gripper"] = 1.0

            # --- Update targets: velocity control from ACTUAL position ---
            for j in JOINTS:
                k = f"{j}.pos"
                if j in inputs:
                    targets[k] = clamp(j, actual[k] + inputs[j] * max_step)
                elif j == "shoulder_pan" and abs(actual[k] - targets[k]) < DEADBAND_SHOULDER:
                    # Let the shoulder rest when close enough
                    targets[k] = actual[k]

            # --- Face buttons ---
            cur = [js.get_button(i) for i in range(js.get_numbuttons())]
            if cur[BTN_A] and not prev[BTN_A]:
                for k, v in HOME.items():
                    targets[k] = clamp(k[:-4], v)
            if cur[BTN_B] and not prev[BTN_B]:
                print_positions(targets, actual)
            if cur[BTN_X] and not prev[BTN_X]:
                print("\nExiting.")
                break

            robot.send_action(targets)
            prev = cur
            time.sleep(LOOP_SLEEP)

    pygame.quit()
    print("Done. Arm released.")


if __name__ == "__main__":
    main()