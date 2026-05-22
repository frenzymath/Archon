#!/usr/bin/env bash

set -e

TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"

curl -L https://github.com/frenzymath/Archon/archive/refs/heads/main.tar.gz -o archon.tar.gz
tar -xzf archon.tar.gz
cd Archon-main

if ! command -v python3 &> /dev/null; then
    echo -e "\033[31mError: python3 is not installed. Please install python3 and try again. See : https://www.python.org/downloads/\033[0m"
    exit 1  
fi

if ! python3 -m pip --version &> /dev/null; then
    echo -e "\033[31mError: pip is not available. Please ensure pip is installed and try again. See : https://pip.pypa.io/en/stable/installation/\033[0m"
    exit 1  
fi

python3 -m pip install .
archon setup

cd ~
rm -rf "$TEMP_DIR"