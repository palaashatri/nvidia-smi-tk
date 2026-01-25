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
- 🌐 **Cross-platform** - Compatible with Windows, Linux, and macOS
- 🪶 **Lightweight** - No external dependencies, uses only Python standard library

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
