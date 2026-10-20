import time
import numpy as np
import mujoco
import mujoco.viewer
from pynput import keyboard

# Define the ultra-lightweight string-based MJCF XML model for the SO-101.
SO101_XML = """
<mujoco model="so101_virtual_leader">
    <compiler angle="degree" coordinate="local"/>
    <option gravity="0 0 0" timestep="0.02"/>
    
    <worldbody>
        <light directional="true" diffuse=".8 .8 .8" specular=".2 .2 .2" pos="0 0 5" dir="0 0 -1"/>
        <geom type="plane" size="1 1 .01" rgba=".2 .2 .2 1"/>
        
        <!-- Robot Base -->
        <body name="base_link" pos="0 0 0.05">
            <geom type="cylinder" size="0.05 0.05" rgba="0.1 0.1 0.1 1"/>
            
            <!-- Joint 1: Shoulder Rotation / Pan -->
            <body name="shoulder_pan" pos="0 0 0.05">
                <joint name="joint_1" type="hinge" axis="0 0 1" range="-170 170" limited="true"/>
                <geom type="box" size="0.03 0.03 0.06" rgba="0.8 0.2 0.2 1"/>
                
                <!-- Joint 2: Shoulder Pitch -->
                <body name="shoulder_pitch" pos="0 0 0.06">
                    <joint name="joint_2" type="hinge" axis="0 1 0" range="-90 90" limited="true"/>
                    <geom type="box" size="0.02 0.02 0.1" rgba="0.2 0.8 0.2 1" pos="0 0 0.05"/>
                    
                    <!-- Joint 3: Elbow Pitch -->
                    <body name="elbow" pos="0 0 0.1">
                        <joint name="joint_3" type="hinge" axis="0 1 0" range="-140 140" limited="true"/>
                        <geom type="box" size="0.02 0.02 0.08" rgba="0.2 0.2 0.8 1" pos="0 0 0.04"/>
                        
                        <!-- Joint 4: Wrist Pitch -->
                        <body name="wrist_pitch" pos="0 0 0.08">
                            <joint name="joint_4" type="hinge" axis="0 1 0" range="-90 90" limited="true"/>
                            <geom type="box" size="0.015 0.015 0.04" rgba="0.8 0.8 0.2 1" pos="0 0 0.02"/>
                            
                            <!-- Joint 5: Wrist Roll & Gripper Parent -->
                            <body name="wrist_roll" pos="0 0 0.04">
                                <joint name="joint_5" type="hinge" axis="0 0 1" range="-150 150" limited="true"/>
                                <geom type="box" size="0.03 0.01 0.02" rgba="0.2 0.8 0.8 1" pos="0 0 0.01"/>
                            </body>
                        </body>
                    </body>
                </body>
            </body>
        </body>
    </worldbody>
    
    <actuator>
        <position name="act_j1" joint="joint_1" kp="100"/>
        <position name="act_j2" joint="joint_2" kp="100"/>
        <position name="act_j3" joint="joint_3" kp="100"/>
        <position name="act_j4" joint="joint_4" kp="100"/>
        <position name="act_j5" joint="joint_5" kp="100"/>
    </actuator>
</mujoco>
"""

model = mujoco.MjModel.from_xml_string(SO101_XML)
data = mujoco.MjData(model)

# Shared tracking states
active_joint_idx = 0  
joint_names = ["Joint 1 (Base Pan)", "Joint 2 (Shoulder Pitch)", "Joint 3 (Elbow)", "Joint 4 (Wrist Pitch)", "Joint 5 (Wrist Roll)"]
target_positions = np.zeros(5)

print("====================================================")
print("  SO-101 VIRTUAL LEADER KEYBOARD SIMULATION RUNNING")
print("====================================================")
print(" Controls (Works globally from any window focus):")
print("   [1, 2, 3, 4, 5] : Select which joint to control")
print("   [Up Arrow]      : Move selected joint POSITIVE (+)")
print("   [Down Arrow]    : Move selected joint NEGATIVE (-)")
print("====================================================")
print(f"Currently controlling: {joint_names[active_joint_idx]}")

# Background listener for cross-platform hardware keystrokes
def on_press(key):
    global active_joint_idx, target_positions
    try:
        # Check number keys 1 through 5 via character evaluation
        if key.char in ['1', '2', '3', '4', '5']:
            active_joint_idx = int(key.char) - 1
            print(f"Switched control to: {joint_names[active_joint_idx]}")
    except AttributeError:
        # Check special key states (Arrows)
        if key == keyboard.Key.up:
            target_positions[active_joint_idx] += 5.0
            print(f"{joint_names[active_joint_idx]} target -> {target_positions[active_joint_idx]}°")
        elif key == keyboard.Key.down:
            target_positions[active_joint_idx] -= 5.0
            print(f"{joint_names[active_joint_idx]} target -> {target_positions[active_joint_idx]}°")

# Start background keyboard threading hook
listener = keyboard.Listener(on_press=on_press)
listener.start()

# Launch the visualizer loop smoothly 
with mujoco.viewer.launch_passive(model, data) as viewer:
    while viewer.is_running():
        step_start = time.time()
        
        with viewer.lock():
            # Convert targets to radians and pass to virtual actuators
            data.ctrl[:] = np.radians(target_positions)
            
            # Step physical position updates
            mujoco.mj_step(model, data)
            
        viewer.sync()
        
        # Enforce execution frame loops timing target
        time_until_next_step = model.opt.timestep - (time.time() - step_start)
        if time_until_next_step > 0:
            time.sleep(time_until_next_step)

# Cleanup the threading hooks when exiting simulation window loops
listener.stop()