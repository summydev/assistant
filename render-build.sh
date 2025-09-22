#!/usr/bin/env bash
set -o errexit

# Install PortAudio so PyAudio can build
apt-get update
apt-get install -y portaudio19-dev

# Continue with normal build
pip install -r requirements.txt
