# NVIDIA-SMI GUI Monitor

A real-time graphical interface for monitoring NVIDIA GPU status using `nvidia-smi`, with color-coded metrics, process table, and power limit adjustment.
Now with DARK MODE!

<img width="1769" height="1099" alt="image" src="https://github.com/user-attachments/assets/28221887-f2b2-4dd8-a11e-d759bbab4773" />



## Overview

This application provides a user-friendly GUI that displays key NVIDIA GPU information in real-time, updating every 2 seconds. It is built with Python's Tkinter library and uses the `nvidia-smi` command to fetch GPU status and running processes.

## Features

- 🔄 **Real-time monitoring** - Updates every 2 seconds automatically
- 🎨 **Color-coded display** - Visual indicators for utilization, memory, temperature, and power draw
- 📊 **Process monitoring** - Table of running GPU processes (PID, name, memory usage)
- ⚡ **Power limit control** - Adjust GPU power limits with validation (requires admin privileges)
- 📋 **Full nvidia-smi output** - Collapsible section showing complete nvidia-smi information
- 🛡️ **Error handling** - Graceful handling of missing drivers or command failures
- 📈 **History graphs, tray icon, alerts and CSV/JSON export** - available when the optional `matplotlib`, `pystray`/`pillow` and `plyer` packages are installed
- 🌐 **Windows and Linux** - macOS is not supported: current Macs have no NVIDIA driver or `nvidia-smi`
- 🪶 **Lightweight** - the core runs on the Python standard library alone; extras are optional (`pip install -r requirements.txt`)

## Known limitations

- **One GPU only.** With two or more NVIDIA GPUs the readings get mixed up, because the output of all GPUs is parsed as one line.
- Fields that `nvidia-smi` reports as `[N/A]` or `[Not Supported]` (often fan speed or power on laptops) can stop the display from updating.
- On Linux, changing the power limit runs `sudo nvidia-smi -pl`, which has no terminal to ask for your password when the app is started from a desktop menu. Start the app from a terminal, or with the rights needed to change power limits.

## Roadmap

In priority order — each is something a user would notice:

1. **Multi-GPU support:** one card per GPU, plus a combined view.
2. **Reliable readings on laptops:** show "N/A" for unsupported fields instead of freezing.
3. **Power limit changes that just work:** a polkit (`pkexec`) prompt on Linux and a UAC prompt on Windows, with the allowed min/max shown next to the slider.
4. **Per-process actions:** filter the process table and end a process from it.
5. **Logging mode:** record readings to CSV over a session and show the graph for that period.
6. **Installers:** signed Windows `.exe` and a Linux AppImage on every release.

## Project Structure

```
pytk-nvidia-smi-gui/
├── App.py              # Main application file
├── requirements.txt    # Project dependencies (Python stdlib only)
├── LICENSE            # MIT License
└── README.md          # This file
```

## Prerequisites

### Hardware Requirements

- NVIDIA GPU with compatible drivers installed
- NVIDIA driver version that supports `nvidia-smi` command

### Software Requirements

- Python 3.6 or higher
- Tkinter (usually included with Python installation)
- NVIDIA drivers with `nvidia-smi` utility

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Author

Palaash Atri

## Acknowledgments

- NVIDIA for the `nvidia-smi` utility
- Python Software Foundation for Tkinter
- The open-source community for inspiration and feedback

---

Made with ❤️ for GPU monitoring enthusiasts
