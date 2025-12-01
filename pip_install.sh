#!/bin/bash
find $(pwd) -name "requirements.txt" | sed -e 's/^/-r /' > tmp.txt
pip install -r tmp.txt
