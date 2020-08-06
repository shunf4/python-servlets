#!/bin/bash
pip install -r <(find $(pwd) -name "requirements.txt" | sed -e 's/^/-r /')
