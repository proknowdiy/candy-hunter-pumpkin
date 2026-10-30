# Candy-Hunter Pumpkin 🎃

**An AI-powered interactive Halloween pumpkin by Pro Know**

Candy-Hunter Pumpkin uses the D-Robotics RDK X5 to detect candy with an AI model and a camera. An ESP32-C3 handles the pumpkin's interactive hardware, while a browser-based interface displays the camera stream and detection results.

The project combines edge AI, computer vision, embedded control, LED effects, servo actuation, and audio feedback.

- **Creator:** Pro Know
- **YouTube:** https://www.youtube.com/@ProKnow
- **Main computer:** D-Robotics RDK X5
- **Microcontroller:** ESP32-C3
- **Camera:** OV5647 MIPI camera
- **Code license:** MIT — see [LICENSE](LICENSE)

## Features

- Candy detection using an AI model accelerated by the RDK X5 BPU
- OV5647 MIPI camera capture
- Browser-based live preview and detection interface
- WebSocket video streaming
- USB serial communication between the RDK X5 and ESP32-C3
- Servo and LED control through the microcontroller
- Audio feedback based on the pumpkin's operating state

## Project Structure

```text
candy-hunter-pumpkin/
├── app/
│   └── candy_hunter_pumpkin.py
├── arduino/
│   └── candy_hunter_pumpkin_esp32c3/
│       └── candy_hunter_pumpkin_esp32c3.ino
├── assets/
│   └── audio/
│       ├── audio.mp3
│       └── trick or treat1.mp3
├── docs/
├── models/
│   ├── candy_classes.names
│   └── weights_bayese_640x640_nv12.bin
├── web/
│   └── index.html
├── .gitignore
├── LICENSE
├── README.md
└── run.sh
```

## 1. Hardware Requirements

### Main hardware

- D-Robotics RDK X5 developer board
- OV5647 MIPI camera compatible with the board's camera interface and software image
- ESP32-C3 development board
- Servo motor(s) and the mechanical pumpkin mechanism
- Addressable LEDs supported by the firmware
- USB data cable connecting the ESP32-C3 to the RDK X5
- Suitable power supplies and wiring for the servos, LEDs, and controller

### Hardware notes

**Camera:** This project uses an OV5647 MIPI camera. Connect it to the appropriate supported camera connector and verify that the installed RDK software image supports the sensor. Camera connector compatibility and sensor support can depend on the board, carrier board, and software image.

**ESP32-C3:** Connect the controller to the RDK X5 using a USB data cable. A charge-only cable will not work for serial communication.

**Power:** Servos can draw substantial current, especially when starting or stalled. Do not power high-current servos or LED loads directly from an ESP32-C3 GPIO pin. Use an appropriately rated supply and a suitable common ground where required.

Check the voltage, current, pinout, and wiring requirements of your specific hardware before powering it.

## 2. Software Prerequisites

### RDK X5 environment

Run the Python application on the RDK X5, not on a regular desktop Python installation.

Required components include:

- A compatible RDK X5 Ubuntu software image
- The RDK camera runtime and Python bindings, including `hobot_vio` / `libsrcampy`
- The RDK BPU inference runtime, including `hbm_runtime`
- The model's required preprocessing and postprocessing support
- Python 3 and the Python modules used by the application
- `pyserial` for USB serial communication
- `mpg123` for MP3 playback
- A working PulseAudio-compatible audio output session
- Network connectivity between the RDK X5 and the computer used to view the web interface

Some RDK-specific libraries are supplied by the board's software environment. They are not necessarily available from standard PyPI packages. Use the installation instructions for your particular RDK software image rather than trying to install every dependency with `pip`.

### Python dependencies

The application uses RDK-specific camera and inference components, serial communication, image processing, and web streaming.

Before installing anything, inspect the imports in `app/candy_hunter_pumpkin.py` and check which packages are already installed in your RDK environment.

For example, check Python and serial support with:

```bash
python3 --version
python3 -m pip show pyserial
```

If `pyserial` is missing and your environment permits package installation, install it using the package manager or Python environment recommended for your RDK image.

Do not replace RDK-provided camera or BPU libraries with arbitrary versions from PyPI.

### Audio prerequisites

The application uses `mpg123` to play the project's MP3 files.

Check whether it is available:

```bash
which mpg123
```

Audio playback also depends on the audio session being available to the user running the application. The existing application is configured for the `sunrise` user's PulseAudio session in the tested setup.

If the application is started as another user, including `root`, audio may fail if that user's audio-session environment is unavailable.

## 3. Prepare the ESP32-C3 Firmware

The Arduino sketch is located at:

`arduino/candy_hunter_pumpkin_esp32c3/candy_hunter_pumpkin_esp32c3.ino`

### Arduino IDE requirements

Install:

1. Arduino IDE
2. Espressif's ESP32 board package
3. The **FastLED** library
4. The **ESP32Servo** library

Install FastLED and ESP32Servo through Arduino IDE Library Manager. Select an ESP32-C3-compatible board entry that matches your development board.

The exact board selection and upload options can depend on the ESP32-C3 board being used. Verify the board's USB and boot-mode instructions if uploading fails.

### Upload procedure

1. Connect the ESP32-C3 to your computer using a USB data cable.
2. Open the `.ino` sketch in Arduino IDE.
3. Select the appropriate ESP32-C3 board.
4. Select the correct USB port.
5. Compile the sketch.
6. Upload the firmware.
7. Open Serial Monitor at **115200 baud** if you need to inspect firmware messages.
8. After upload, connect the ESP32-C3 to the RDK X5 for normal operation.

Keep the serial commands, state messages, and controller behavior compatible with the Python application when modifying the firmware.

## 4. ESP32-C3 USB Serial Port on the RDK X5

The ESP32-C3 appears as a serial device when connected to the RDK X5 over USB.

Depending on the USB device enumeration order, the port may be named `/dev/ttyACM0`, `/dev/ttyACM1`, or another device path.

Do not assume that the port number will always be the same.

### Find the port

Connect the ESP32-C3 to the RDK X5 and run:

```bash
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

You can also inspect recent kernel messages:

```bash
dmesg | tail -n 40
```

If necessary, run the port-listing command before and after connecting the controller to identify which device appears.

### Verify the configured port

The Python application must open the actual device path assigned to the ESP32-C3.

Inspect the application for the serial configuration:

```bash
grep -nE 'ttyACM|ttyUSB|serial.Serial|SERIAL' app/candy_hunter_pumpkin.py
```

If the code is configured for `/dev/ttyACM0` but the controller appears as `/dev/ttyACM1`, update the application's serial-port configuration to match the detected device. Prefer a single clearly documented configuration value rather than changing unrelated code.

### Serial permissions

If the application reports `Permission denied`, inspect the device permissions:

```bash
ls -l /dev/ttyACM0
groups sunrise
```

Replace `/dev/ttyACM0` in the diagnostic command if the device has a different name.

On many Ubuntu systems, serial access is provided through the `dialout` group. If needed, an administrator can add the intended user to the appropriate group:

```bash
sudo usermod -aG dialout sunrise
```

Log out and back in for the new group membership to take effect. Check your RDK image's actual device permissions and group configuration before applying changes.

Avoid running the entire application as root just to work around serial permissions.

## 5. OV5647 Camera Setup and Troubleshooting

The application uses the RDK camera runtime to capture frames from the OV5647 MIPI camera.

Before running the application:

- Confirm that the camera is connected to the correct supported MIPI connector.
- Check that the installed RDK software image supports the OV5647 sensor.
- Confirm that the camera can be initialized using the camera tools or demo supplied with your RDK image.
- Ensure that another application is not already using the camera.
- Use the camera resolution and pixel format expected by the application.

The tested application reported a camera stream of **1280 × 720 at approximately 30 FPS**. Actual capture settings and performance can vary with the software image and camera configuration.

If camera initialization fails, first test the camera independently using the tools provided with your RDK software image. Then inspect the application logs for camera initialization and pipeline errors.

A camera pipeline shutdown warning during exit does not by itself establish that normal capture failed. Check whether the camera initialized, frames were received, and inference worked before diagnosing the warning.

## 6. Model and Class Labels

The model files are stored in `models/`:

- `weights_bayese_640x640_nv12.bin`
- `candy_classes.names`

The model is expected to be compatible with the RDK X5 BPU runtime and the application's preprocessing and postprocessing implementation.

The current pipeline resizes frames to a **640 × 640** model input before inference. The model, labels, and decoding logic must remain compatible with each other.

If you replace the model, verify:

- Input dimensions and pixel format
- Model compatibility with the RDK X5 BPU runtime
- Class count and class-label order
- Output tensor format and decoding logic
- Confidence and non-maximum-suppression thresholds

A model trained or exported for a different runtime may not work by simply replacing the `.bin` file.

## 7. Run Candy-Hunter Pumpkin

### Clone the Repository

Clone the project to your RDK X5:

```bash
git clone https://github.com/YOUR-GITHUB-USERNAME/candy-hunter-pumpkin.git
```

Navigate to the project directory:

```bash
cd candy-hunter-pumpkin
```

Run the launcher:

```bash
./run.sh
```

Alternatively, run the Python application directly:

```bash
python3 app/candy_hunter_pumpkin.py
```

The launcher is optional. It starts the Python application from the project directory.

If execution permission is missing, you can run:

```bash
bash run.sh
```

### Open the web interface

Find the RDK X5's IP address on your network, then open this address in a browser on a computer connected to the same network:

`http://<RDK-X5-IP>:8080/`

Replace `<RDK-X5-IP>` with the board's actual IP address. If the application is configured to use another port, use that port instead.

The web interface is served by the Python application, so a separate Nginx installation is not required.

### Stop the application

Press `Ctrl+C` in the terminal running the application and allow it to shut down.

## 8. Audio and Controller Behavior

The Python application plays audio based on state messages received from the ESP32-C3.

The tested firmware/application interaction includes states such as:

- `STATE:STANDBY`
- `STATE:HUNT`
- `STATE:OPENING`
- `STATE:MOUTH_OPEN`
- `STATE:CLOSING`
- `STATE:COOLDOWN`

The application uses the MP3 files in `assets/audio/` for the corresponding audio behavior.

If audio does not play:

- Confirm that `mpg123` is installed.
- Check that the audio files exist in `assets/audio/`.
- Verify that the user running the application can access the audio output session.
- Test audio playback separately from the application.
- Check terminal output for audio or subprocess errors.

Do not rename or remove audio files without checking the paths used by the Python application.

## 9. Troubleshooting

### The ESP32-C3 is not detected

- Use a USB data cable.
- Try another supported USB port.
- Check `dmesg` after reconnecting the controller.
- Confirm that the ESP32-C3 has entered normal operating mode after firmware upload.

### Serial communication does not work

- Verify the actual `/dev/ttyACM*` or `/dev/ttyUSB*` device.
- Confirm that the Python application uses that port.
- Check permissions for the user running the application.
- Confirm the baud rate is 115200 on both sides.
- Check that the uploaded firmware uses the expected command and state protocol.

### The camera fails to initialize

- Check the MIPI connection and camera compatibility.
- Test the OV5647 with the RDK-provided camera tools.
- Check whether another process is using the camera.
- Review the application logs for sensor and pipeline initialization errors.

### The browser cannot connect

- Confirm that the Python application is running.
- Use the correct RDK X5 IP address and server port.
- Check that the client computer and RDK X5 can communicate over the network.
- Check firewall rules if the board is reachable but the web port is not.

### The application cannot load the model

- Confirm that the model and label files exist in `models/`.
- Check file permissions.
- Verify that the model is compatible with the installed BPU runtime.
- Check that the model path is derived from the project directory and has not been hardcoded to a different machine's location.

## 10. Build Your Own Version

You are welcome to adapt the project for your own pumpkin enclosure and hardware.

Ideas for customization include:

- Changing the enclosure and mechanical design
- Adding or changing LED effects
- Adjusting servo motion and interaction timing
- Training or adapting a candy-detection model
- Modifying the web interface
- Adding other detection classes or interaction behaviors

When modifying the project, keep the Python application and ESP32-C3 firmware protocol synchronized. Test changes to servo movement and power wiring carefully.

## 11. Open-Source License and Third-Party Assets

The project code is released under the MIT License. See [LICENSE](LICENSE) for the full license text.

The MIT License allows reuse, modification, and redistribution of the covered code, subject to its terms.

Third-party libraries, the AI model, audio files, and other external assets may have separate copyright or license terms. The project license does not automatically grant redistribution rights for those materials. Check their respective terms before redistributing them.

## Credits

Created by **Pro Know**.

If you build your own version, consider sharing your implementation and improvements with the community.
